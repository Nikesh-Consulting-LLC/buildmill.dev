# Agent servers — design

**Date:** 2026-07-25
**Phase:** 26
**Status:** approved (design), implementation in `phase-26-agent-servers`

An **agent server** is a machine an admin registers by SSH so that Build Mill can install,
run, update and retire coding agents on it. The manager gives a host, a username and a
credential; everything after that — dependencies, the supervisor code, agent identities,
service units, updates, health — is done by the app over SSH.

This is not a new runner architecture. The supervisor runner (`apps/runner/supervisor`,
[Supervisor runner design](2026-07-19-supervisor-runner-design.md)) was already built to be
dropped on any machine with Python and a worker token, with everything else configured
server-side. What was missing was the act of dropping it there. This phase automates that,
and the fleet management that follows from owning the machine.

## Why it exists

Today capacity is a human errand: SSH in, install Python and Node, install a coding CLI,
mint a worker token on Settings, paste it into an env file, write a service unit, and
remember to redo all of it on the next release. That errand does not scale past the boxes
one person happens to remember, and a machine that drifts a version behind fails in ways
that look like the work being wrong rather than the machine being stale.

## Shape

`servers` stays exactly what it is — the org-scoped SSH host registry from US-1.28
(`019_servers.sql`): host, port, username, auth method, credentials in the private `data`
bucket, host-key trust-on-first-use, and the SFTP/terminal bridge in `apps/api/app/ssh.py`
and `routers/servers.py`. None of it changes. Three tables layer on top:

```
servers (existing SSH host)
└── agent_servers            1:1 — this host is an agent machine
    ├── agent_slots          N   — one agent = one identity + one systemd service
    │     └── workers / principals (existing) ─► runner WS, capability matrix, console
    └── agent_server_jobs    every SSH-side action, with a live log
```

Reusing `servers` rather than inventing a parallel registry is the single most consequential
choice here: credentials, host-key TOFU, the SSH/SFTP transport, the browser terminal and
the file manager are all already built, audited and gated. A second registry would mean a
second place SSH secrets live — the exact thing `019_servers.sql` warns against — for no
gain beyond a tidier-looking table.

### `agent_servers`

| Column | Purpose |
|---|---|
| `server_id` (unique) | The SSH host this is. One agent config per machine |
| `workdir` | Install root, default `/opt/buildmill` |
| `status` | `new · provisioning · ready · degraded · error · removed` |
| `bundle_hash`, `agent_version` | What is installed. The hash **is** the version (see below) |
| `modules[]` | Which coding-agent CLIs to install (`claude`, `grok`, `opencode`) |
| `extra_packages[]`, `setup_commands` | Admin-declared extras, re-applied on every update |
| `allow_agent_sudo` | Off by default; on, writes a sudoers drop-in for the agent user |
| `slot_template` (jsonb) | Runner config + capability grants inherited by new slots |
| probe columns | `os_release`, `cpu_count`, `mem_total_mb`, `mem_free_mb`, `disk_total_gb`, `disk_free_gb`, `load_avg`, `last_probe_at`, `probe_error` |

Org-scoped with RLS. Reads for any org member; writes gated on the `manage_org` capability
(`087_role_capability_layer.sql`) — an agent server is org infrastructure, and the request
was explicitly that admins add them.

### `agent_slots`

One row per agent on the machine: `agent_server_id`, `index` (unique per host), `name`,
`worker_id`, `principal_id`, `service_name` (`buildmill-agent@N`), `workspace_path`,
`desired_state` (`paused · enabled · stopped`), observed `service_state`, reported
`agent_version`.

A slot's worker and principal are ordinary rows. That is the point: Team, the capability
matrix (`worker_capabilities`), the runner console, presence and pool claiming all operate
on them with **no changes**. An agent installed this way is indistinguishable from one
installed by hand, which is what keeps this phase additive rather than a fork.

### `agent_server_jobs`

`kind` (`provision · add_slot · update · restart · remove_slot · teardown · probe`),
`status`, an appended `log`, `started_by`, timings, `error`. The shape mirrors
`deployment_runs` (`021_deployment_runs.sql`): a `log text` column appended as the job runs,
streamed to the browser over Supabase Realtime. Jobs are **idempotent** — re-running
`provision` on a half-installed box resumes rather than restarts.

## On the machine

```
/opt/buildmill/
  app/<bundle_hash>/        supervisor code, pushed from the API host
  app/current -> …          symlink the units point at
  venv/                     python3 -m venv + pip install -r requirements.txt
  env/1.env … env/N.env     0600: FACTORY_API_URL + one worker token
  agents/1/workspace …      per-slot checkout root (RUNNER_WORKSPACE)
/etc/systemd/system/buildmill-agent@.service   one template unit, N instances
```

Services run as a dedicated non-login **`buildmill`** system user. `sudo` is used only by
the install steps, via the SSH user; the agent user gets none unless `allow_agent_sudo` is
turned on. Default-off is deliberate: an agent that can `apt-get` can also rewrite the
machine that audits it, and the failure mode of leaving it off (a build fails naming the
missing package) is legible, while the failure mode of leaving it on is not.

### Code delivery and versioning

`api` tars its own `apps/runner` tree, hashes the tar, uploads it over the SFTP channel it
already holds, extracts to `app/<hash>/` and repoints `current`. Consequences worth naming:

- The agent version always matches the API that manages it — they ship from the same tree.
- The machine needs **no GitHub credential**, which a `git clone` of a private repo would
  have forced onto every box.
- **The hash is the version.** There is no version file to bump and therefore no way for
  the recorded version and the installed code to disagree. Drift is `installed ≠ current`.

## Trust boundary

The only secret that lands on an agent machine is **one worker token per slot**, in a 0600
env file. Not a model key — the brain and every CLI reach models through the LLM gateway
with short-lived scoped keys minted per run (US-10.3). Not a GitHub credential — the
supervisor clones through the factory git proxy, authenticating with that same worker
token. A compromised agent box costs N revocable tokens and nothing else.

Provisioning needs `sudo` on the SSH user. Preflight checks for it explicitly and fails
with a clear message rather than half-installing; where the server uses password auth the
stored password is fed to `sudo -S` and never written to the job log. Job logs are redacted
for tokens and passwords before they are stored, because they are readable by any org
member while the credentials that produced them are not.

## Pause — the one change outside the new tables

Slots come up **paused**: the service runs and connects, but claims nothing until an admin
enables it. `workers.status` is only `active | revoked`, and revoking would invalidate the
token just written to the box, so pause needs its own knob:

**`runner_config.paused boolean`**, enforced in the claim path (a paused worker is offered
no work) and pushed over the existing `config.update` socket frame so a connected
supervisor stops pulling immediately. The same knob drains a slot before an update restarts
it, so it earns its place twice.

## Lifecycles

### Provision

One job, resumable at any step:

1. **Preflight** — `/etc/os-release` is Debian-family, systemd present, `sudo` works, arch,
   free disk. Anything else fails here, named, with nothing installed.
2. **Base packages** — `git curl python3 python3-venv python3-pip`, Node 20 via NodeSource.
3. **Extras** — the host's `extra_packages` and `setup_commands`.
4. **Agent CLIs** — the selected modules, versions recorded.
5. **Bundle** — push, extract, venv, `pip install -r requirements.txt`.
6. **Units** — install `buildmill-agent@.service`, `daemon-reload`.
7. **Slots** — per requested slot: mint principal + worker token, write the 0600 env file,
   `systemctl enable --now`, wait for the control socket to connect.
8. **Verify** — probe; host goes `ready`.

A failure stops at its step, names it, and leaves the host `error` with the log intact.

### Update

Each host reports its installed bundle hash; when it differs from the API's current one the
host card shows a drift badge. Update re-pushes the bundle, re-applies steps 2–4
idempotently, then walks the slots **one at a time**: pause → wait for the in-flight run to
finish (cooperative stop, 10-minute ceiling, then report and skip rather than kill work) →
restart → restore the slot's previous state. The host keeps serving from its other slots
throughout.

Automatic fleet-wide update was rejected: a bad release would take every agent down at once
with no staged rollout, and the drift badge already makes staleness impossible to miss.

### Health probe

One small SSH script: os, cpu count, free RAM, free disk on the workdir, load,
`systemctl is-active` per unit, CLI versions, installed hash. It runs on the sweep loop
that already exists in `apps/api/app/main.py` (staggered, ~5 minutes per host), on demand,
and at the end of every job. Two things it catches that the WebSocket cannot:

- **A service that died without ever connecting** — raised as a `runner_incidents` row and
  notified, like any other runner fault. A presence-only view is blind to this: no socket
  looks identical to a machine that was never asked to run anything.
- **Capacity** — adding a fourth slot to a 2-core box, or one with 3 GB free, warns first.

### Teardown

Pause → drain → `systemctl disable --now` → remove units and env files → revoke tokens →
mark the agent principals inactive → optionally wipe the workdir. The `agent_servers` row
is **soft-removed** so its job history survives and re-provisioning is one click; the
`servers` row is untouched. Identities are kept, not deleted, because past runs name the
agent that did them and a roster tidied at the cost of history is a bad trade.

## Surfaces

- **`/agent-servers`** — one card per host: status, agents (`3 · 2 enabled`), version/drift
  badge, load · RAM · disk, last probe.
- **`/agent-servers/[id]`** — Overview (Provision, Update, Probe), Agents (slot table with
  state, current work item, service state, version; pause/enable/restart/remove; bind an
  existing agent), Setup (workdir, modules, extras, slot template), Activity (job history +
  live log), Danger zone. **Terminal and Files are the existing panels**, pointed at the
  underlying server.
- **Add flow** — pick a registered server *or* enter host/user/credential inline → workdir,
  modules, extras, slot count → Provision, watching the log.
- **Team** — agent rows gain their host; the runner console gains "runs on *bravo* · slot 2"
  with pause/restart in place.

## Out of scope

- Windows hosts (Linux + systemd only; preflight refuses the rest).
- Container-isolated agents — a different runner architecture, not an increment on this one.
- Creating cloud VMs. You bring the machine.
- Metrics history and charts; the probe stores current values only.
- Sharing one host across organizations.

## The build — ten stories

| # | Story |
|---|---|
| 26.1 | Register an agent server — entity, page, add flow, preflight |
| 26.2 | Provision the machine — job model, live log, base toolchain, bundle push, unit template |
| 26.3 | Agent CLIs and extra packages, re-appliable |
| 26.4 | Agent slots — identities, env files, services, come up paused |
| 26.5 | Slot controls — enable/pause/restart, bind an existing agent (`paused` enforced at claim) |
| 26.6 | Host slot template — runner config and capability grants inherited by new slots |
| 26.7 | Health probe, capacity guard, dead-service incident |
| 26.8 | Update the fleet — drift badge, rolling drained update |
| 26.9 | Remove a slot, tear down a host |
| 26.10 | Agent servers on Team and the runner console |

`APPLICATION.md` and `ARCHITECTURE.md` updates ride along in each story rather than as an
eleventh.
