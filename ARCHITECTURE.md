# Software Factory Architecture

How the factory's components fit together. The phased roadmap (what gets built when) lives in [README.md](README.md). For what the application currently does — its surfaces, domain objects, lifecycles, and rules — see [APPLICATION.md](APPLICATION.md). Architectural patterns are modeled on the nexdb.io project (`D:\Github\NexDB\nexdb.io`): Supabase as the system of record, a deliberately thin FastAPI backend, and a trusted component on the operator's machine that does the work the cloud can't.

## Components

```
[ BROWSER ]
     │  HTTPS
     ▼
[ web — Next.js ]
     │   │  Supabase JS SDK (auth + CRUD under RLS, Realtime status updates)
     │   └──────────────► [ Supabase ]   Auth · Postgres · Storage · Realtime
     │                       projects, tasks, runs, gate results, events
     │  Bearer JWT
     ▼
[ api — FastAPI ]  ── verifies JWT via JWKS · reserved for orchestration only
     │
     ├──► provision ────► [ agent server — SSH-managed machine ]
     │                        │  N × buildmill-agent@N systemd units
     │                        └─ each one a supervisor runner, below
     │
     ├──► dispatch ─────► [ runner — operator-side worker ]
     │                        │  invokes provider CLI (Claude Code) in a repo checkout
     │                        │  captures stdout + diff, pushes branch
     │                        └─ reports back via authenticated callback endpoint
     │
     ├──► [ GitHub App ]      branches, PRs, merges, webhooks (CI / check results)
     │
     └──► [ Provider adapters ]   Claude (Phase 1) · Groq / Ollama (Phase 3)
```

- **web** — Next.js (App Router), TypeScript, Tailwind, shadcn/ui, TanStack Query. Authenticates against Supabase Auth and talks to Supabase **directly** for plain CRUD (projects, tasks, gate config) under RLS via the Supabase JS SDK. Subscribes to Supabase Realtime for live task-status changes (queued → running → in review → merged / failed) so the board updates without polling. Calls `api` (Bearer JWT) only for orchestration actions: dispatch a task, approve/reject a review, trigger a merge.
- **api** — FastAPI (Python 3.12, Pydantic), kept deliberately thin. Verifies JWTs server-side via JWKS. It exists for work that genuinely needs a server: dispatching runs to the runner, receiving runner callbacks, GitHub App operations (PR create/merge), webhook ingestion, provider routing, and — for registered deployment servers (us-1.28+) — the **SSH/SFTP bridge**: `api` is the only component that can read a server's stored credentials (private `data` bucket, service role) and open an SSH connection on the operator's behalf. It bridges an interactive PTY to the browser over a WebSocket (terminal, us-1.29), performs SFTP file operations and zip extraction (file manager, us-1.30; text editor, us-1.46), and enforces host-key trust-on-first-use. Credentials never cross to the browser; only terminal I/O and file bytes do. It does **not** re-wrap CRUD that Supabase already exposes. The frontend consumes it through types generated from its OpenAPI schema (`openapi-typescript`), not hand-written DTOs.
- **runner** — a worker process on the operator's machine (where repos and provider CLIs live). It polls or receives dispatched runs, checks out the target repo on a task branch, invokes the provider CLI (Claude Code headless for Phase 1), captures stdout and the resulting diff, pushes the branch, and reports the outcome through an authenticated callback to `api`. The runner is the **only** component that touches local repo checkouts and provider credentials — the cloud never holds them (the same trust split as NexDB's data gateway). **Phase 10** graduates it into a **supervisor runner** (`apps/runner/supervisor`, run via `python -m supervisor`): a server-controlled agent that holds a persistent WebSocket to `api`, is configured entirely server-side (which agent modules — Claude Code / Grok Build / OpenCode — which model, concurrency, policy), reasons with a server-hosted LLM brain, holds **no model secrets** (a server LLM gateway keys the brain and the CLIs), runs an audited/policy-gated shell, and self-repairs — see [Supervisor Runner design](docs/superpowers/specs/2026-07-19-supervisor-runner-design.md). The MCP/HTTP worker path (Cursor, humans in IDEs) is unchanged; the old headless polling script is deprecated.
- **agent servers (Phase 26)** — machines the operator registers by SSH so `api` can *install* runners on them rather than waiting for someone to. `api` pushes a content-hashed tar of its own `apps/runner` tree over the existing SFTP channel, installs the toolchain and the coding-agent CLIs, and runs N supervisors as `buildmill-agent@N` systemd units — each with its own principal, its own worker token, and its own workspace, so Team, the capability matrix and pool claiming see ordinary agents. The machine holds **one worker token per slot and nothing else**: models come through the LLM gateway, git through the factory proxy. Health is a read-only SSH probe on the existing liveness sweep; updates are a drained rolling restart; teardown revokes tokens and removes the units. See [Agent servers design](docs/superpowers/specs/2026-07-25-agent-servers-design.md).
- **GitHub** — the source of truth for code. Branches, PRs, reviews, and CI live there; the factory stores links and status mirrored via GitHub App webhooks, never copies of code.

## Monorepo structure

```
software-factory/
├── apps/
│   ├── web/           # Next.js (App Router)
│   ├── api/           # Python FastAPI backend
│   └── runner/        # Operator-side worker (invokes provider CLIs)
├── infra/
│   └── supabase/      # DB migrations, seed data
├── stories/           # User stories — one file per story, stage folders
└── docs/              # Architecture & design docs
```

The runner lives in this monorepo (unlike NexDB's separate gateway repo) — it shares the task/run contracts with `api`, and there is a single operator, so a separate repo would add friction without a boundary to protect.

## Data model (core tables)

| Table | Purpose |
|---|---|
| `projects` | A product + its linked GitHub repo, gate config, provider routing defaults |
| `tasks` | User story with acceptance criteria; status: queued → running → gates → in review → merged / failed / needs-fixes |
| `task_events` | Append-only log of everything that happened on a task |
| `runs` | Each provider execution: provider, input context, stdout, diff ref, tokens/cost, outcome |
| `gate_results` | Test / lint / security-scan outcomes per run (Phase 2) |
| `reviews` | Manager approve/reject decisions with comments; rejection feedback is attached to the retry run |

All tables are org-scoped and RLS-protected from the first migration, even while there is a single user — retrofitting tenancy later is explicitly out of scope (NexDB Principle: multi-tenancy is foundational, not a phase).

## Request & data flow

1. **Define** — the manager creates a project (linked GitHub repo) and a task (user story + acceptance criteria) in `web`, written directly to Supabase under RLS.
2. **Dispatch** — the manager (or later, automation) dispatches the task; `api` creates a `runs` record and notifies the runner. The runner checks out the repo, invokes the provider CLI with the story as context, captures the diff, pushes a branch, and calls back with the result. Status changes flow to the UI via Realtime.
3. **Gates (Phase 2)** — after a run produces code, the runner executes the project's configured gates (unit tests, lint, security scan). Any failure blocks the task from reaching review, flags it `needs-fixes`, and the retry run carries the specific failure output as context — retries are informed, never blind.
4. **Review** — the manager opens the review panel: diff and original story side by side, gate results inline. Approve → `api` merges the PR via the GitHub App. Reject with a comment → the task returns to the provider with that feedback attached.
5. **Deploy (Phase 4)** — merge triggers UAT deploy via GitHub Actions; UAT results surface in the same dashboard; a manual go-ahead gate stands before production.

## Provider abstraction (Phase 3)

One task-in / result-out contract that every provider adapter implements, so adding a provider never touches the orchestrator:

- **Input**: story + acceptance criteria, repo context, prior failure feedback (if a retry).
- **Output**: diff/branch ref, stdout/log, token usage, outcome.

Claude (via Claude Code CLI) is the only adapter in Phase 1. Groq and Ollama follow behind the same contract, with a routing policy (task type → provider), automatic logged fallback on timeout/error, and per-task manual override.

## Security & trust boundaries

- **Credentials are two-tier.** *Coding-agent* credentials (repo access, provider CLI auth) live on the runner, never in Supabase or `api` — the cloud asks the runner to do work; it cannot reach repos or coding CLIs itself. *Thinking-task* LLM keys (the global provider configured in Settings — triage, drafting, summaries) live cloud-side in **Supabase Vault**, write-only from the browser via a security-definer RPC and readable only by server-side code; the UI sees at most the key's last 4 characters.
- Runner callbacks are authenticated (shared-secret / signed) — the callback endpoint is the runner's only inbound surface on `api`.
- GitHub access is via a GitHub App with scoped permissions; merges happen server-side in `api` only on an explicit manager approval.
- Every state change lands in `task_events` — the audit trail (Phase 6) is a query, not a bolt-on.

## Simplifications & deferred complexity

Deliberate choices to keep the surface small — don't pull these forward without a real need:

- **Build less API.** Supabase (Postgres + Auth + Storage + RLS + Realtime) is the system of record; FastAPI is reserved for dispatch, callbacks, GitHub ops, provider routing, and the SSH/SFTP bridge to registered deployment servers (which genuinely needs a server-side connection holding credentials the browser must never see).
- **No queue infrastructure.** Task dispatch is a DB-backed handoff (status columns + Realtime/polling), single task at a time. Redis/Celery only if Phase 7 concurrency is ever actually needed.
- **One provider first.** The adapter contract is defined in Phase 1 but only Claude implements it; multi-provider routing waits for Phase 3.
- **Single user, tenant-ready.** Supabase Auth with one account; no roles/teams UI. But every table is org-scoped + RLS from migration 001.
- **Generate types, don't hand-maintain them.** Frontend API types from the OpenAPI schema; Supabase types from its schema generator.
