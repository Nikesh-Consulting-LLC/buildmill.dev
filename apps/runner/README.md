# Supervisor runner

The operator-side worker for Build Mill. Drop it on any machine with Python and
a **worker token** — everything else (which agent modules it may use, which
model routes to the brain and each run kind, concurrency, autonomy policy) is
configured **server-side** from the app's **Runners** page.

## Run it

```bash
export FACTORY_API_URL=https://<your-build-mill-api>
export FACTORY_WORKER_TOKEN=<mint one on Settings → Workers>
python -m supervisor            # from apps/runner/
```

That's the whole install by hand. **Or don't**: an admin can register the
machine on **Agent servers** (Phase 26) and Build Mill installs all of this
over SSH — dependencies, the agent CLIs, this code, a `buildmill-agent@N`
systemd unit per agent, each with its own identity and token — then keeps it
updated and reports its health. The machine still holds exactly one secret per
agent: its worker token.

The runner:

- opens a persistent **control socket** (WebSocket + JSON-RPC) to the server and
  shows up on the **Runners** page (US-10.1);
- receives its **config** on connect and live over the socket (US-10.2);
- **pulls work** from the pool over the existing HTTP contract, and for each
  claimed run the **brain** (a server-configured LLM) picks the module, drives
  it, and submits (US-10.6);
- holds **no model secrets** — the brain and the agent CLIs reach models through
  the server **LLM gateway** with short-lived scoped keys (US-10.3);
- runs a **controlled shell** with full autonomy but **server-side policy + a
  complete audit trail** (US-10.7), and **self-repairs** when things break
  (US-10.8);
- classifies failures **work-fault vs runner-fault** so a broken machine is
  visible and you get pinged (US-10.11).

## Agent modules

Built-in modules (enable them per runner on the Runners page):

| Module | CLI | Notes |
|---|---|---|
| `claude` | Claude Code | `claude -p … --output-format text` |
| `grok`   | Grok Build  | `grok -p … --output-format plain --always-approve` (installed from its own GitHub release, not npm — see `agent_provision.GROK_CLI_INSTALL_CMD`) |
| `opencode` | OpenCode  | `opencode run --format json …` |
| `sim`    | — | simulated; for testing the pipeline with no CLI/model |

Adding an agent is a drop-in module under `supervisor/modules/` (or a package
published to the `buildmill.runner.modules` entry-point group) implementing the
`AgentModule` contract — no change to the runner core.

## Configuration knobs (env)

Only connection + per-CLI overrides live on the machine; behavior is server-side.

- `FACTORY_API_URL`, `FACTORY_WORKER_TOKEN` — connection.
- `RUNNER_CLAUDE_CMD` / `RUNNER_CLAUDE_ARGS`, `RUNNER_GROK_CMD` / `RUNNER_GROK_ARGS`,
  `RUNNER_OPENCODE_CMD` / `RUNNER_OPENCODE_ARGS` — override the CLI binary/flags.
  These are appended **last**, so they can override anything the app resolved —
  including `--permission-mode bypassPermissions`, which the Claude module now
  sets itself (US-47.1). Overriding that one breaks the run: measured against
  the CLI, `default`, `acceptEdits` and `plan` let **zero** MCP calls through in
  headless mode, so the agent cannot read its own work item.
- `RUNNER_WORKSPACE` — checkout root (default `apps/runner/supervisor/workspace`).
- `CLAUDE_CODE_OAUTH_TOKEN` — the machine-held Claude **subscription** credential
  (US-52.1). Generate it with `claude setup-token` (a browser login; the token
  lives a year) and set it in this process's environment. It is only consulted
  when the agent's settings put Claude runs on **Claude Code — OAuth**: in that
  mode the supervisor injects *no* `ANTHROPIC_API_KEY`/`ANTHROPIC_BASE_URL`, so
  the CLI falls through its credential chain to this token (or to a `claude
  /login` state for the account this runner runs as) and bills the
  subscription. In the default **Claude Code — API** mode the gateway env
  shadows it and nothing changes. There is no fallback in either direction: a
  subscription run with no credential fails with the CLI's own auth error
  rather than silently minting a metered key. On a fleet-provisioned agent
  server, prefer the guided flow on the machine's page (US-52.3) — it runs
  `claude setup-token` on the box through the in-app terminal and installs
  the token into every agent slot env for you.

> The old headless `runner.py` (RUNNER_PROVIDER=…) is **deprecated** (US-10.10)
> and kept only for the local e2e harness.
