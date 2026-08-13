# Supervisor Runner — Design

**Date:** 2026-07-19
**Status:** Draft (approved in brainstorm; pending spec review)
**Phase:** 10 — Supervisor runner
**Supersedes:** `apps/runner` (the headless polling script, US-1.10 / US-3.5 / US-2.15)

## Context

Today's runner ([apps/runner/runner.py](../../../apps/runner/runner.py)) is a thin, "dumb" polling loop: it lists the pool over HTTP, claims a run, invokes one hard-wired provider (`provider_sim` or `provider_claude`), and submits. Everything about it is configured on the *operator's* machine via env vars (`RUNNER_PROVIDER`, `RUNNER_CLAUDE_CMD`, provider auth), and it can only drive Claude Code. Adding Grok Build or OpenCode means writing new provider files and re-deploying the script; changing behavior means touching the machine.

We want the opposite: a **supervisor runner** — an autonomous, server-controlled worker that operators drop onto any machine with nothing but a token, and everything else (which agent modules, which model, how much it may do, how it recovers) is configured and controlled from the Build Mill server.

### Locked decisions (from the 2026-07-19 brainstorm)

1. **The runner is a supervisor agent.** It has its own agentic loop, powered by a **server-configured LLM** ("the brain"). The brain decides what to do, drives coding **modules** as tools, diagnoses failures, and self-repairs the environment. Coding modules (Claude Code, Grok Build, OpenCode) are tools it calls, not the top-level driver.
2. **Replace the runner, reuse the pool.** The new `apps/runner` replaces the polling script but keeps claiming from the **same work pool** and reuses the **`workers` / token registry** (migration 039). MCP workers (Cursor, humans in IDEs) keep using today's HTTP/MCP contract unchanged.
3. **Runner pulls work; the socket carries control.** Work acquisition stays on today's HTTP claim contract (`/api/v1/worker/*`). A **persistent secure socket** carries config, commands, telemetry, and the brain's LLM inference. No work-dispatch push.
4. **The server provides all model access.** The runner machine holds **no model keys**. A server-hosted **LLM gateway** (real provider keys in Vault) serves both the brain and the modules; the machine's only secret is its worker token — which also already authenticates the factory git remote. Zero-secret machines.
5. **Full shell autonomy, server-policed.** The brain may run any shell command on its machine; the **server** holds the policy and the **audit trail**. Every command is streamed to the server for audit. Self-repair runs within this (fully autonomous, fully logged) model.
6. **Transport:** WebSocket + JSON-RPC 2.0 (rides the existing HTTPS ingress; precedent in the us-1.29 PTY-over-WebSocket bridge).
7. **Modules:** a Python plugin contract + registry; each module is built on two runner **primitives** — `run_api(...)` and `run_shell(...)`.
8. **LLM access:** one server-hosted gateway (uniform for brain and modules).

## Goals

- An operator installs a Python app on any machine, gives it one worker token + the server URL, and it connects — no per-machine model keys, no per-machine agent config.
- The runner is a **supervisor**: it reasons about the claimed work, picks and drives an agent module, watches for breakage, and repairs itself, using a server LLM as its brain.
- **Agents are modules.** Adding Grok Build / OpenCode / a future agent is dropping in a module that implements one contract — no change to the runner core or the server.
- **All configuration and control live server-side** once a runner is connected: which modules are enabled, which model routes to the brain vs each module, concurrency, autonomy policy, and project capabilities.
- The existing autonomous pipeline (pool → claim → context → submit, factory git remote, review/merge) keeps working; this changes *who claims* and *how it's controlled*, not the contract.

## Non-goals (this phase)

- Replacing the MCP / HTTP worker path (Cursor, human-in-IDE). Those are untouched.
- Server-**push** work dispatch (runner still pulls/claims).
- Sandboxing / containerizing the runner (full-autonomy-on-your-machine is the chosen model; container isolation is a later story if wanted).
- Multi-tenant hosted runners. A runner belongs to one org (its token's org), same as today.
- Replacing the LLM-provider-routing / Vault infrastructure (US-3.17 / migration 002); the gateway builds on it.

## Architecture

```
 operator machine (zero model secrets)                 Build Mill server
┌───────────────────────────────────────┐        ┌──────────────────────────────────┐
│  apps/runner  (Python supervisor)      │        │  api — FastAPI                    │
│                                        │        │                                   │
│  ┌──────────────┐   pulls work (HTTP)  │  HTTP  │  /api/v1/worker/*  (pool/claim/   │
│  │ work loop    │◀────────────────────────────▶│     context/submit)   [unchanged] │
│  └──────┬───────┘                      │        │                                   │
│         │ claimed run                  │        │  /api/v1/runner/socket  (WS)      │
│  ┌──────▼───────────────────────┐      │   WS   │  ┌────────────────────────────┐   │
│  │ supervisor brain (agentic)   │◀────────────────▶│ control plane              │   │
│  │  - plan / drive / diagnose   │      │ JSON-  │  │  - config push             │   │
│  │  - calls primitives + module │      │ RPC    │  │  - command audit sink      │   │
│  └──┬────────────┬──────────────┘      │        │  │  - policy gate             │   │
│     │            │                     │        │  │  - LLM inference relay      │  │
│  run_api      run_shell               │        │  └─────────────┬──────────────┘   │
│     │            │                     │        │                │                  │
│  ┌──▼────────────▼──────────────┐      │        │        ┌───────▼────────┐         │
│  │ module host + registry       │      │  HTTPS │        │  LLM gateway   │──▶ Vault │
│  │  claude · grok · opencode …  │──────────────────────▶│ (provider-shaped│   keys  │
│  └──────────────────────────────┘      │ (base_ │        │  endpoints)    │         │
│                                        │  url +  │        └────────────────┘         │
│  only secret on disk: worker token     │  scoped │                                   │
│  (also git-remote auth)                │  key)   │                                   │
└───────────────────────────────────────┘        └──────────────────────────────────┘
```

Three channels between runner and server:

1. **Work (HTTP, pull):** the existing `/api/v1/worker/*` claim contract. The supervisor lists the pool, claims, pulls the context bundle, and submits — exactly as `provider_claude` does today.
2. **Control (WebSocket + JSON-RPC 2.0, bidirectional):** config push, command audit stream, policy checks, brain LLM inference relay, telemetry/heartbeat, presence.
3. **Model access (HTTPS):** the brain and every module call the **LLM gateway** at a server URL, authenticated by a short-lived scoped key the server injects — never a provider key on disk.

### Component: supervisor runner (`apps/runner`, Python)

A restructured Python app (still a single deployable, runnable anywhere with Python 3.12). Internally:

- **Connection manager** — opens and maintains the WebSocket, does the auth handshake (worker token → session), reconnects with backoff, and re-syncs config on reconnect. Presence/heartbeat lives here.
- **Config cache** — the server-pushed config for this runner (enabled modules, model routes, concurrency, policy). Held in memory; refreshed on connect and on live `config.update` notifications. The machine never has a config file that matters.
- **Work loop** — pulls from the pool over HTTP (respecting `concurrency` from config), claims, and hands each claimed run to a supervisor session. Heartbeats the claim (today's lease model) while work runs.
- **Supervisor brain** — the agentic loop. Given a claimed run's context bundle, it reasons (via the server LLM through the control channel or gateway), selects a module, drives it, inspects the result, and on failure diagnoses and invokes self-repair before retrying or reporting. Bounded by policy (max iterations, budget).
- **Module host + registry** — discovers and instantiates `AgentModule` plugins; exposes the two primitives to modules and to the brain.
- **Primitives** — `run_shell(...)` (controlled shell, streamed to the audit sink, policy-gated) and `run_api(...)` (HTTP call, e.g. to the LLM gateway or a module's own API).

### Component: control plane (server, WebSocket + JSON-RPC)

New FastAPI WebSocket endpoint `/api/v1/runner/socket`. JSON-RPC 2.0 framing over the socket, so both sides can issue requests, get correlated responses, and send fire-and-forget notifications.

**Auth handshake:** first frame from the runner carries the worker token (same `X-Worker-Token` value, sent in the WS subprotocol/first message, validated by `db.get_worker_by_token`). The server binds the socket to that `worker` row + org, records presence, and returns the initial config. Invalid/revoked token → close.

**Methods (runner → server):**
- `runner.hello { agent_versions, modules_available, host_info }` → `{ config, session_id }`
- `llm.infer { route, messages, ... }` → `{ completion, usage }` (brain inference relayed to the gateway with Vault keys; keeps brain reasoning server-side-keyed)
- `command.audit { run_id?, argv, cwd, started_at }` → `{ allow: bool, reason? }` (policy check + audit record; the runner streams `command.output`/`command.exit` notifications after)
- `telemetry { ... }` / `heartbeat` (presence + lease liveness)

**Methods (server → runner):**
- `config.update { config }` (live config change — new module enabled, model route changed, policy tweaked)
- `command.run { argv, cwd }` (operator-initiated remote command — the "controlled shell" from the server side, e.g. a manual repair)
- `runner.drain` / `runner.reload` (graceful stop / re-read)

**Policy + audit:** every `run_shell` invocation (module-, brain-, or server-initiated) sends a `command.audit` request *before* executing; the server applies the runner's policy (default: allow) and writes an audit row, then the runner streams stdout/stderr/exit as notifications. Full-autonomy means the default policy allows everything; the value is the **complete, server-side audit trail** and a kill-switch (policy can flip to deny / require-approval per runner).

### Component: LLM gateway (server)

A server-hosted proxy exposing **provider-shaped endpoints** (Anthropic Messages, OpenAI Chat Completions, xAI) under `/api/v1/llm-gateway/*`. It:

- Holds no keys itself — reads the org's provider keys from **Vault** (the US-3.17 / migration 002 mechanism) at call time.
- Authenticates callers with a **short-lived scoped key** minted per run/session and injected into modules + used by the brain. The scope is `{org, worker, run, model-route}`; it can't be replayed to hit an arbitrary model.
- Records usage (tokens, cost) back onto the run (reusing the `tokens_in/out`, `cost_usd` fields the submit contract already carries).

At **dispatch of a module**, the supervisor injects into the module's environment the gateway `base_url` + the scoped key + the resolved model id — e.g. `ANTHROPIC_BASE_URL` + `ANTHROPIC_API_KEY` for Claude Code, `XAI_API_KEY` (+ base URL) for Grok Build, provider config for OpenCode. Modules that accept a base-url override (all three chosen CLIs do) therefore need no local auth.

### Component: module system

A **module = an agent**. Contract (`apps/runner/modules/base.py`):

```python
class AgentModule(Protocol):
    name: str                       # "claude", "grok", "opencode"
    capabilities: set[str]          # {"code", "plan", "prd", "breakdown"}

    def model_env(self, route: ModelRoute) -> dict[str, str]:
        """Env to inject so this agent uses the server gateway (base_url + key + model)."""

    async def execute(self, ctx: RunContext, prim: Primitives) -> ModuleResult:
        """Do the work for one run kind, using prim.run_shell / prim.run_api.
        Returns the same shape today's ProviderResult carries
        (plan / prd / stories / branch_ref / test_cases / stdout / error)."""
```

- **Discovery:** modules register via an entry-point group (`buildmill.runner.modules`) or a `modules/` package scan, so a new agent is a drop-in file/package — no core edits. The registry reports `modules_available` to the server on `hello`; the server's config says which are **enabled** for this runner.
- **CLI modules** (`claude`, `grok`, `opencode`) implement `execute` by shelling out through `prim.run_shell` — reusing today's `provider_claude` git plumbing (clone via factory remote, checkout `factory/issue-<id>`, run CLI, commit, push) lifted into a shared base. Their only differences are the argv builder + output parsing (Claude/Grok: `-p <prompt> --output-format text`; OpenCode: `run --format json "<prompt>"`).
- **Direct-API modules** (future) implement `execute` via `prim.run_api` against the gateway, no subprocess.
- **The two primitives are the whole surface** a module (or the brain) has onto the machine and network: `run_shell` (controlled, audited, policy-gated) and `run_api` (HTTP, used for the gateway and any module API).

## Data flow

### Normal run

1. **Connect** — runner opens the socket, `runner.hello` with its token; server validates, records presence, returns config (enabled modules, model routes, concurrency, policy).
2. **Pull + claim** — the work loop lists the pool and claims a run over HTTP (unchanged), pulls the context bundle (story, criteria, plan, branch, git remote).
3. **Supervise** — the brain reads the context, picks the module for the run kind (per config: e.g. `code`→grok, `plan`→claude), injects gateway env, and calls `module.execute`.
4. **Execute** — the module clones via the factory remote, runs its CLI through `run_shell` (each command audited), produces the artifact (diff pushed to `factory/issue-<id>`, or plan/prd/stories).
5. **Submit** — the supervisor submits over the existing HTTP contract; the factory verifies the branch, opens the PR / commits direct, and the review pipeline proceeds unchanged. Usage flows back through the gateway's records.

### Self-repair loop

When a module fails (CLI non-zero, timeout, dirty/broken checkout, missing dep), the brain receives the failure and diagnoses it with the server LLM, then acts through `run_shell`:

- transient (network / lock) → wait + retry;
- broken checkout → re-clone / `git reset --hard` / re-checkout the branch;
- dependency error → run the project's install command (from build_config);
- module wedged → kill + restart the module;
- unrecoverable / policy-denied / budget exceeded → **submit an error** (never vanish — same guarantee as today's runner) with the diagnosis in `stdout`, so the run returns to the pool / `failed` and the manager sees why.

Every step is bounded (max repair attempts, max iterations, token budget from policy) and fully audited.

## Configuration surface (server-side)

The heart of "all configuration and control on the server side." Per **runner** (a `workers` row of type `autonomous`), stored server-side and pushed over the socket:

| Setting | Meaning |
|---|---|
| `enabled_modules` | Which registered modules this runner may use (subset of what it reported available). |
| `model_routes` | Map of `{brain, code, plan, prd, breakdown}` → provider/model, resolved through the gateway. The brain and each run kind can use different models. |
| `concurrency` | How many pool items this runner works at once. |
| `autonomy_policy` | Shell policy (`allow` / `require-approval` / `deny` patterns), max repair attempts, max iterations, per-run token/cost budget, and **health thresholds** (consecutive-failure / repair-rate limits before a runner is flagged unhealthy). |
| `capabilities` | Which projects / run kinds this runner may claim — **reuses** the existing worker-capabilities model (US-3.12, migration `worker_allowed_for_run`). |

Config is delivered on `hello` and updated live via `config.update`; changing a runner's model or enabling a module is a server-side edit that takes effect without touching the machine.

## Security & trust boundaries

- **Zero model secrets on the machine.** Only the worker token is on disk; it authenticates the socket *and* the factory git remote (today's Basic-auth pattern). Provider keys stay in Vault; the gateway injects only short-lived scoped keys.
- **Socket auth** is the worker token (hashed lookup, same as HTTP). A revoked worker's socket is closed; a revoked token can't reconnect.
- **Full-autonomy shell is server-policed + fully audited.** Default policy allows all commands, but every command is recorded server-side with argv/cwd/output/exit, and policy can be flipped per runner to require-approval or deny — a kill switch for a misbehaving runner.
- **Gateway scoping** prevents a leaked module key from being used beyond its run/model route.
- **Org isolation** is unchanged: a runner belongs to its token's org; the pool, git remote, and gateway are all org-scoped under existing RLS.

## Data model additions

New migrations (numbered after the current head; applied live + types regenerated, per repo convention):

- `runner_config` — per-worker config (the table above); RLS org-scoped; managed by `manage_work`-capable members.
- `runner_sessions` — presence/connection log: worker, connected_at, disconnected_at, host_info, agent versions. Powers the "who's connected" UI.
- `runner_command_audit` — append-only shell audit: session, run_id (nullable), argv, cwd, started_at, exit_code, truncated output ref, policy_decision. Org-scoped, read-only to the client.
- `llm_gateway_keys` (or reuse an ephemeral-token table) — minted scoped keys with `{org, worker, run, route}` + expiry; never returns provider material.
- `runner_incidents` — runner-**fault** events (kind, message, `run_id`?, session, at) distinct from per-run work failures; org-scoped, client-read-only. A derived per-runner **health** state (healthy / degraded / unhealthy) is computed from recent incidents + repair rate + presence (a column on `runner_config` or a view, not a separate table). Feeds the management surface and notifications.

Modules themselves are **code, not data** — the registry is in the runner; the server only stores which of the reported modules are enabled (a column/array on `runner_config`).

## Error handling

- **Never vanish.** Every claimed run ends in a submit — success or an error carrying the brain's diagnosis. (Preserves the today-runner guarantee and the US-3.4 lease-expiry auto-submit backstop.)
- **Socket loss ≠ work loss.** If the socket drops mid-run, the work loop keeps the HTTP claim alive via heartbeat; the runner buffers audit/telemetry and flushes on reconnect. If the brain can't reach the LLM relay, it falls back to a bounded, module-only execution (drive the configured module once, no agentic supervision) and flags the degraded mode — work still lands.
- **Policy-denied command** → treated as an unrecoverable step for that path; the brain either finds another route or submits an error naming the denied command.
- **Reconnect** re-runs `hello` and re-syncs config, so a config change during a disconnect is picked up.
- **Fault classification — work-fault vs runner-fault.** Every error submit and every exhausted repair is tagged: a **work-fault** (the story / plan / code is wrong → re-dispatch or fix the item) or a **runner-fault** (the machine or environment is broken — clone failure, disk full, missing module, credential expiry, repeated self-repair on the same host → fix the runner, not the story). The tag comes from the brain's diagnosis plus the command-audit exit signals, rides the submit, and drives a derived per-runner **health** state (healthy / degraded / unhealthy). A runner-fault is recorded as a `runner_incidents` event and raises a manager notification (reusing US-9.12), so a chronically failing runner is visible and fixable — distinct from a genuinely hard work item. This is what makes remote failures **diagnosable and actionable server-side**, not just "the run failed."

## Testing strategy

- **Module contract tests** — each module (claude/grok/opencode) against a fake `Primitives` (recorded `run_shell`/`run_api`), asserting the right argv and result parsing. Extends today's `tests/test_provider_claude.py`.
- **Supervisor loop tests** — brain loop with a stubbed LLM relay and a stub module: happy path, failure→repair→retry, budget/iteration caps, unrecoverable→error-submit.
- **Control-plane tests** — WebSocket handshake (valid/invalid/revoked token), config push + live update, `command.audit` allow/deny, audit records written.
- **Gateway tests** — scoped-key minting/expiry/scope enforcement; usage recorded to the run; no provider material ever returned.
- **Integration** — a `sim` module (today's `provider_sim` ported) drives a full pool→claim→supervise→submit against a test server, proving the pipeline end-to-end with no real CLI or model.
- **Backward-compat** — the existing HTTP/MCP worker path keeps its tests green (this phase doesn't touch it).

## Phase 10 story breakdown (foundation-first)

1. **us-10.1 — Supervisor runner scaffold + socket connection.** Restructure `apps/runner`; WebSocket + JSON-RPC client; token handshake; presence/heartbeat; reconnect. Server `/api/v1/runner/socket` endpoint + `runner_sessions`.
2. **us-10.2 — Runner config store + push.** `runner_config` table; server delivers config on `hello` and via `config.update`; runner config cache. Management surface stub.
3. **us-10.3 — LLM gateway.** Provider-shaped endpoints reading Vault keys; scoped ephemeral keys; usage recorded to runs. Brain inference relay (`llm.infer`).
4. **us-10.4 — Module system: contract, registry, primitives.** `AgentModule`, entry-point discovery, `run_shell` (audited) + `run_api`; shared git/prompt base lifted from `provider_claude`.
5. **us-10.5 — Built-in modules: Claude Code, Grok Build, OpenCode.** Three modules on the contract, each using the gateway env; the `sim` module ported for tests.
6. **us-10.6 — Supervisor brain loop.** Agentic claim→plan→drive→inspect→submit over the existing pool contract; iteration/budget caps.
7. **us-10.7 — Controlled shell + command audit stream.** `command.audit` policy check, `runner_command_audit`, server-initiated `command.run`, per-runner policy (allow/require-approval/deny).
8. **us-10.8 — Self-repair loop.** Diagnose-and-recover playpath (retry, re-clone/reset, reinstall deps, restart module); bounded; unrecoverable→error-submit.
9. **us-10.9 — Runner management UI.** On the Team/Workers surface: connected runners + presence, config editor (modules/model routes/concurrency/policy), live command audit, health.
10. **us-10.11 — Runner health & fault classification.** Tag every error submit / exhausted repair as work-fault vs runner-fault; derive a per-runner health state + `runner_incidents`; surface health on the management UI (us-10.9) and raise a manager notification (US-9.12) on a runner-fault. (Id is the next free number; build order slots it here, before retiring the old runner.)
11. **us-10.10 — Retire the old polling runner.** Replace `apps/runner/runner.py` + `provider_*.py` with the module-based supervisor; migrate docs/onboarding snippets; keep MCP/HTTP worker path unchanged.

**Phase 10 exit test:** stand up a fresh machine with only Python + the runner + a worker token and the server URL. From the server, enable the Grok Build and Claude Code modules, route `code`→Grok and `plan`→Claude, set concurrency 1. Dispatch one plan run and one code work item; watch the runner connect, claim, supervise each with the configured module, push a branch through the factory remote, and land in review — with no model keys on the machine, every shell command visible in the server-side audit, and one deliberately-broken checkout self-repaired (re-clone) mid-run without operator intervention. Then induce a **runner-fault** (e.g. remove a module's CLI or fill the disk): the run is tagged `runner-fault` (not a work failure), the runner flips to **unhealthy**, and a manager notification fires — so you know it's the box to fix, not the story.

## Open questions (resolve during planning)

- **Brain inference path:** relay over the socket (`llm.infer`) vs. the brain calling the gateway HTTP like modules do. Leaning socket-relay for the brain (keeps reasoning traffic on the audited control channel), gateway-HTTP for modules. Confirm in us-10.3.
- **Scoped-key lifetime:** per-run vs per-session. Per-run is tighter; per-session is fewer mints. Decide in us-10.3.
- **Degraded (no-brain) fallback** default: drive the configured module once, or refuse and return to pool. Leaning "drive once, flag degraded."
- **Policy default** ships as `allow-all + audit` per the brainstorm; confirm the require-approval UX in us-10.7.
