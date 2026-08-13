# Agent & Worker Connectivity — Design

**Date:** 2026-07-15
**Status:** Approved design, pre-implementation
**Scope:** How coding agents (Claude Code, Cursor, OpenCode, the autonomous runner) connect to the factory to claim and complete work items — flexibly, so any given item can be done by an autonomous agent or by a person driving their own tool.

> **Revision (2026-07-15, same day):** the factory is now **the central place all work flows through** — including git. Workers clone and push through a factory-hosted **git smart-HTTP proxy** (us-3.8) authenticated by their worker token; no worker ever holds GitHub credentials, and hand-back reconciliation is driven by the factory's own push log instead of GitHub webhooks or a sync button. All sections below reflect this revision.

## Problem

Today the factory has exactly one way to get work done: the manager dispatches a run, and the operator-side runner claims it over `/runner/*` endpoints (shared secret) and executes a provider CLI. There is no way for a human to say "I'll take this one in Cursor" and have the item tracked through the same pipeline — claimed, worked, submitted, reviewed, merged — nor a way to plug in a second agent tool without writing a new code path.

## Core reframe: providers become workers

A **worker** is anything that can claim work from the factory and hand it back:

- the runner executing Claude Code headless (today's path),
- a future runner process executing OpenCode or another CLI,
- a human driving Claude Code / Cursor / OpenCode interactively on their own machine.

The factory's contract is worker-shaped. The Phase-3 "provider abstraction" survives *inside* autonomous workers — the runner still chooses which CLI to execute — but the factory itself neither knows nor cares; it sees workers claiming runs and submitting results. Adding a new agent tool becomes a config entry (mint a worker token), not a code change.

## Decisions made during brainstorming

| Question | Decision |
|---|---|
| Human handoff mechanism | **MCP server** exposed by the factory; any MCP-capable tool mounts it |
| Work matching | **Pull from a ready pool**, first-come-first-served; claiming locks the item |
| Autonomous vs. human paths | **One surface for all** — the runner is refactored into just another worker |
| Worker identity | **Worker registry + per-worker tokens** (hashed at rest, last4 shown, revocable) |
| Hand-back | **Explicit MCP/REST submit + push-detection safety net** — every push flows through the factory's git remote, so lease expiry auto-submits pushed work; no webhooks, no sync button. Idempotent |
| Git access | **Factory git remote** — a smart-HTTP proxy in `api` (`/git/*`); workers authenticate with their worker token, the GitHub App installation token is injected server-side and never leaves `api` |
| Work kinds | **Both plan runs and code runs** flow through the pool |
| Delivery | **Remote MCP layered over a REST worker API, hosted in `api`** (approach A); a local stdio shim (approach B) is a later follow-up only if branch-setup friction proves real |

## Architecture

```
[ human in Cursor / Claude Code / OpenCode ]          [ runner (autonomous worker) ]
        │  MCP (streamable HTTP, /mcp)                        │  REST
        │  git over HTTPS (/git/*)                            │  git over HTTPS (/git/*)
        │  worker token (header / git Basic auth)             │  worker token (header / git Basic auth)
        ▼                                                     ▼
    [ api — FastAPI:  /worker/* REST  +  /mcp MCP wrapper  +  /git/* smart-HTTP git proxy ]
              │ token → worker → org scoping                  │ GitHub App token injected upstream
              ▼                                               ▼
    [ Supabase: workers, runs (worker_id, lease), issues ]  [ GitHub — source of truth for code ]

    push to factory/issue-<id> ──► recorded on the claimed run ──► lease expiry ──► reconciler auto-submits
```

The MCP tools are 1:1 thin wrappers over the REST endpoints — one contract, two doors. Humans configure a URL + token in their tool; the runner (headless) calls REST directly and skips MCP. Git is the third door, same token: every clone, fetch, and push goes through the factory's `/git/*` proxy — no worker ever talks to GitHub directly.

## Data model

### New table: `workers`

Org-scoped, RLS via `public.is_org_member(org)` from the first migration, like every table.

| Column | Notes |
|---|---|
| `id` | uuid PK |
| `org_id` | FK, RLS scope |
| `name` | e.g. "Runner (Claude Code)", "Kaushlesh — Cursor" |
| `type` | `autonomous` \| `human` |
| `user_id` | nullable; set for human workers |
| `token_hash` | SHA-256 of the token; the token itself is shown **once** at creation |
| `token_last4` | for display: `Token set · …abcd` |
| `status` | `active` \| `revoked` |
| `last_seen_at` | updated on any authenticated call |
| `created_at` | |

Token handling follows the repo's write-only secret pattern: no endpoint, response, or log ever returns the token after creation; the UI shows at most the last4. A settings page lists workers with revoke/regenerate.

### Changes to `runs`

| Column | Notes |
|---|---|
| `worker_id` | nullable FK — who claimed it |
| `claimed_at` | |
| `claim_expires_at` | the lease |

No new status vocabulary. `queued` **is** "in the pool"; `running` **is** "claimed". The existing flow `queued → running → in review → merged/failed` is unchanged. The `provider` column stays for autonomous runs (which CLI ran); human runs record `provider = 'human'`, with the worker row carrying the real identity.

### Claim leases

- Autonomous workers heartbeat every few minutes; the lease extends on each heartbeat.
- Human claims get a long default lease (24 h). There is no MCP heartbeat tool — instead, **any authenticated worker call touching the run** (`get_work_context`, `submit`, `release`) refreshes `last_seen_at` and extends the lease. The REST `heartbeat` endpoint exists for headless workers that make no other calls while a CLI executes.
- A reaper returns expired claims to the pool (`running → queued`) and logs a `issue_events` entry. This folds into the orphaned-run reaper already scoped in us-2.15.

## REST worker API

New FastAPI router `/worker/*`, authenticated by `X-Worker-Token` → worker lookup (active workers only). The existing `/runner/*` endpoints remain until the runner migrates, then retire.

| Endpoint | Behavior |
|---|---|
| `GET /worker/pool` | Claimable (`queued`) runs for the worker's org: run id, kind (`plan` \| `code`), issue title/type, project, repo |
| `POST /worker/runs/{id}/claim` | Atomic claim (`UPDATE … WHERE status='queued'`); sets `worker_id`, `claimed_at`, `claim_expires_at`; returns lease expiry. Loser of a race gets 409 |
| `GET /worker/runs/{id}/context` | Full context bundle (see below) |
| `POST /worker/runs/{id}/heartbeat` | Extends the lease |
| `POST /worker/runs/{id}/submit` | Kind-dependent; see Hand-back |
| `POST /worker/runs/{id}/release` | Returns the run to the pool with a note, logged to `issue_events` |

### Context bundle

Everything an agent or human needs to start, in one call:

- story + acceptance criteria
- PRD excerpt, if the issue belongs to a feature with an approved PRD
- the approved implementation plan + test plan (code runs)
- project guidelines and learnings
- rejection feedback (informed retries, us-1.13)
- repo `owner/name` + default branch
- the **expected branch name**: `factory/issue-<id>`
- the **factory git remote URL** for the project (`/git/<project-id>.git`)

Cloning, branching, committing, and pushing all go through the **factory git remote** (next section) with the same worker token. No worker — human or autonomous — ever holds GitHub credentials; the GitHub App installation token is minted and cached inside `api` and never leaves it. This *strengthens* the trust split: the only repo credential in existence lives server-side, and revoking a worker cuts its git access the same instant it cuts the API.

## MCP server

Mounted in the same FastAPI app (official Python MCP SDK, streamable-HTTP transport at `/mcp`). Tools are 1:1 wrappers over the REST endpoints:

- `list_available_work`
- `claim_work`
- `get_work_context`
- `submit_plan`
- `submit_code_work`
- `release_work`

Client setup is one config entry per tool, e.g.:

```
claude mcp add --transport http factory https://<api-host>/mcp \
  --header "X-Worker-Token: <token>"
```

Cursor (`.cursor/mcp.json`) and OpenCode take the same URL + header. Intended human flow: open the IDE agent, ask "what factory work is available?", claim an item, let the agent pull context and start on the named branch — cloned from the factory remote below.

## Factory git remote (smart-HTTP proxy) — us-3.8

The factory fronts **all repo access** with git's smart-HTTP protocol at `/git/{project_id}.git` — a streaming pass-through proxy to the project's GitHub repo with the App installation token injected upstream. The factory hosts **no repo state**: no mirroring, no bare repos; GitHub remains the source of truth for code, per [ARCHITECTURE.md](../../../ARCHITECTURE.md).

| Route | Behavior |
|---|---|
| `GET /git/{project}.git/info/refs` | Service advertisement (upload-pack / receive-pack) |
| `POST /git/{project}.git/git-upload-pack` | Clone / fetch |
| `POST /git/{project}.git/git-receive-pack` | Push — **policy-checked before forwarding** |

- **Auth**: HTTP Basic, password = worker token — the same us-3.1 identity, so plain `git` works with zero custom tooling, `last_seen_at` updates on git activity, and revocation is immediate.
- **Push policy at the proxy**: receive-pack ref-update commands arrive ahead of the packfile, so the proxy validates before a byte reaches GitHub — every update must be a branch-create or fast-forward on `factory/issue-<id>` for a run **currently claimed by the authenticated worker**. Default-branch writes, other branches, deletions, and force-pushes are refused with readable git errors. This is a guarantee that handing workers any GitHub token could never give.
- **Push log**: every successful push records head SHA + pushed-at on the claimed run and lands in `issue_events` naming the worker — the raw material for hand-back reconciliation (us-3.4).
- **Streaming**: both directions stream without buffering whole packfiles (gzip request bodies, chunked responses); integration-tested with a real git client.
- **Out of scope**: Git LFS, SSH transport, hosting/mirroring, web code browsing.

## Hand-back

### Explicit submit (front door)

`POST /worker/runs/{id}/submit`, kind-dependent:

- **Plan runs**: `plan` + `test_plan` markdown → flows into the existing us-2.5 approval gate unchanged.
- **Code runs**: `branch_ref` — the branch pushed through the factory remote — plus optional notes and test cases (us-1.16). The API verifies the branch via the GitHub App, **opens the PR itself** (or adopts an existing open one for that branch), and pulls the diff from GitHub — workers neither open PRs nor post diffs.

Submit succeeds while the run's `worker_id` is the caller — even if the lease expired, so long as no one else has since claimed it (no lost work for a slow worker). If the reaper already re-queued the run and it is still unclaimed, the same worker claims it again (it's `queued`) and submits. If another worker claimed it in the meantime, the late submit gets a clear 409.

### Push-detection reconciliation (safety net) — no webhooks

Because every push flows through the factory remote, the factory doesn't need GitHub to tell it what happened — **its own push log is the source of truth**. One shared reconciler: given a claimed code run with pushed-but-unsubmitted work, submit it exactly as an explicit submit would (verify branch, open/adopt PR, pull diff, move to *in review*), logging the trigger (`submit` | `lease-expiry`).

- While the claim is alive, pushed work just shows on the work item ("pushed N commits · awaiting submit") — a push is not "done"; workers push WIP freely.
- When a claim **expires with pushed work**, the reaper routes it through the reconciler — auto-submitted, not recycled — so a human who pushes and closes the laptop still lands in review instead of leaving a ghost claim that becomes duplicate work.
- A claim that expires with **no** pushes returns to the pool as before.
- A worker who pushes then **releases** sends the run back to the pool with the branch noted in the release event — the next claimer continues from it; the reconciler never auto-submits an unclaimed run.

Explicit submit and lease-expiry auto-submit are **idempotent** on the same run: whichever arrives later is a no-op. Direct-to-GitHub pushes with personal credentials sit outside the workflow entirely — nothing reconciles them (revisit only if it happens in practice).

### Review pipeline — unchanged

Submitted code → `in review`, same diff-vs-story panel, approve → GitHub App squash merge, reject → feedback attached and a new informed-retry run drops back into the **pool** — claimable by anyone, so the runner can pick up a human's rejected item or vice versa.

## Runner migration

The runner becomes worker #1:

1. Registered as an `autonomous` worker with its own token.
2. Its loop changes from `/runner/claim` polling to `GET /worker/pool` → `claim` → execute provider → `submit` (+ periodic `heartbeat`).
3. Its git work (clone, branch, push) goes through the **factory git remote** with the same worker token — the runner's environment keeps no GitHub credential of its own.
4. The provider contract inside the runner (`execute(input_context) → ProviderResult`, us-1.15) is untouched. us-1.15 (real Claude Code provider) lands independently, before or after this work.
5. Once migrated, the `/runner/*` endpoints and the shared secret retire.

Adding OpenCode later = a second provider inside the runner, or a second runner process with its own worker token. Adding a human = minting a token.

## Edge cases

| Case | Handling |
|---|---|
| Claim race | Atomic conditional update; loser gets 409 and re-lists the pool |
| Lease expiry mid-work | Submit accepted while `worker_id` is still the caller; if re-queued and unclaimed → claim again then submit; reclaimed-by-other → 409 |
| Revoked token | Every endpoint (REST, MCP, git) checks `status='active'`; revocation is immediate |
| Ghost claims | Reaper: expired claim **with** pushes → auto-submit via the reconciler; **without** → back to the pool; both logged to `issue_events` |
| Duplicate hand-back | Explicit submit and lease-expiry auto-submit idempotent on the same run |
| Push to a wrong or unclaimed branch | Refused at the proxy with a readable git error before a byte reaches GitHub — misnamed branches can no longer happen |
| Pushed then released | Run returns to the pool with the branch noted; the next claimer continues from it |
| Cross-org isolation | Token → worker → org scoping on every query; RLS on `workers` verified like every other table |

## Security

- Worker tokens: hashed at rest (SHA-256), shown once, last4 for display, revocable per worker — same write-only posture as LLM keys and server credentials. The same token doubles as the git Basic-auth password: one secret per worker, revocable in one place.
- **No worker holds GitHub credentials.** All git access goes through the factory remote; the GitHub App installation token is minted, cached, and refreshed inside `api` and never appears in a response, error, or log.
- Push policy is enforced at the proxy: claimed `factory/issue-<id>` branches only, fast-forward only — no worker can touch the default branch even by accident.
- The GitHub App remains the only merge/verify/PR authority server-side.
- `workers` is org-scoped with RLS from its first migration; cross-org isolation gets an explicit test.
- Every claim, heartbeat-expiry, push, submit, release, and lease-expiry auto-submit lands in `issue_events` — the audit trail stays a query.

## Testing

API-level tests in the existing `apps/api/tests` pattern:

- claim races (two claimers, one winner)
- lease expiry → reaper → re-claim → submit
- submit verification with a mocked GitHub App (branch exists, PR opened/adopted, diff fetched)
- reconciler: lease expiry with pushes → auto-submit, without pushes → re-queue; idempotency vs. explicit submit; release-after-push
- git proxy: real-git-client clone → branch → push integration, push-policy refusals (wrong branch, unclaimed run, force-push), revoked token, cross-org 404
- token auth: bad token, revoked token, cross-org token
- plan-run submit flowing into the us-2.5 approval gate

MCP tools get thin tests, since they delegate to the same handlers.

## Story breakdown

A new Phase-3 cluster — **"Workers & agent connectivity"** — reframing the old "multi-provider" phase. Slotting into the `users.md` build order is the manager's call.

| Story | Title | Scope |
|---|---|---|
| us-3.1 | Worker registry | `workers` table, RLS, token mint/revoke, settings UI |
| us-3.2 | Worker pool API | pool/claim/context/heartbeat/submit/release + lease reaper |
| us-3.8 | Factory git remote | smart-HTTP proxy `/git/*`, worker-token Basic auth, push policy, push log |
| us-3.3 | Factory MCP server | streamable-HTTP MCP mounted over us-3.2 |
| us-3.4 | Hand-back reconciliation | shared reconciler off the factory push log; reaper auto-submits pushed work |
| us-3.5 | Runner as worker | migrate the runner, retire `/runner/*`; git via the factory remote |
| us-3.7 | Worker connection help | onboarding guides on the Workers page, incl. git remote setup |

## Out of scope

- Concurrent multi-run scheduling policy (Phase 7) — the pool is FCFS, one claim per run.
- Pinning items to a specific worker (pool-only was chosen; revisit if FCFS collides with reality).
- Worker capability matching (e.g. "this worker only serves project X") — YAGNI until there are enough workers to need it.
- A local stdio shim — considered during design, later dropped without being built.
- Git LFS and SSH transport on the factory remote — HTTPS smart-HTTP only until a project needs more.
- GitHub webhooks for the worker flow — superseded by the factory remote's own push log (the us-1.20 webhook pipe stays for issue sync only).
- Payments/quotas per worker, external contributor access.
