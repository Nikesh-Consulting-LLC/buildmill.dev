# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Software Factory: an AI-driven software delivery pipeline with a human manager in the loop — define a user story, an AI provider turns it into a PR, the manager reviews and merges. [ARCHITECTURE.md](ARCHITECTURE.md) describes how the pieces fit (Supabase system-of-record, thin FastAPI for orchestration, operator-side runner, GitHub as source of truth for code). [README.md](README.md) holds the phased roadmap. Before touching a surface (a route, an MCP tool, a status transition) or naming a domain object, check [APPLICATION.md](APPLICATION.md) — the authoritative application reference, ground truth over training-data assumptions or memory of an earlier phase. It holds the overview, the index, and the rules the app can't violate; the detailed catalogs (actors & surfaces, domain objects, lifecycles, interface catalog) live under `docs/application/` — read only the file your change touches.

## Supabase projects & env

Two Supabase projects, and migrations apply to **both**: prod `Software-Factory` (`wdudmfhhqxrqzoyhuzwx`) and dev `build-mill-dev` (`nncquokoblcfcqyajzmk`). Web env lives in `apps/web/.env.local` (copy from `.env.local.example`). Build/test commands are in the machine-maintained Commands section below.

## Stories workflow

All work is story-driven. Stories live in `stories/`, one file per story (`us-N.M-slug.md`); the index is [stories/users.md](stories/users.md).

- **No change without a story.** Every feature, table, migration, or behavior change is defined by a story. If a request isn't covered, add or expand a story first (draft it, get agreement), then implement.
- **Build order = the "Build sequence (open stories)" list** at the top of `users.md` (it orders open work across phases; the per-phase tables are logical groupings kept consistent with it), not numeric id order (ids are stable; new stories take the next free `N.M` and get slotted into the sequence where they'll be worked).
- **Statuses**: `New` (written) → `Testing` (built, awaiting the user's manual UAT) → `Completed` (user confirmed working). Only the user moves a story past Testing — never mark Completed on your own.
- **A story reaches `Testing` with proof attached.** Before flipping the status, verify the built surface (screenshot, page dump, or test output) and record what was verified — and what wasn't — in the story file. Acceptance criteria must pin display semantics ("drift shows as a count, not a hash") precisely enough that UAT is confirm-only; ambiguity here is where rework rounds come from.
- **UAT is batched per release, not chased per story.** Prepare the user's UAT as a checklist (step → expected result, one line per story) covering everything in `Testing`, so one sitting clears the batch.
- **Check for stragglers proactively.** `python scripts/story.py list --status Testing` surfaces them; do this at the start of any stories-related task and offer the batch for confirmation. Confirming and moving still requires the user's go-ahead per story (or an explicit "all of them").
- **Completed work leaves the tree.** When a phase closes, its story files are deleted and the essence lands as a condensed entry in APPLICATION.md's Delivery history (there is no `stories/completed/` folder anymore — the 2026-08-09 backlog close removed it); git history keeps the full text. The `stories/` root is always the remaining work.
- **Keep the story file's `**Status:**` line and its `users.md` rows in sync** — `python scripts/story.py set us-N.M Testing` edits both in one command, and `python scripts/story.py check` audits the whole tree — run it before committing story changes (testing is local by decision; there is no CI gate).
- Each story has acceptance criteria, an explicit out-of-scope list, and dependency links — keep them accurate; if implementation reveals the story is wrong, fix the story in the same change.

## Branching & release

Code flows one way only: **branch → `main` → `prod`**. Nothing else is legal.

- **A full phase gets its own branch.** Branch from `main` (`phase-13-agent-specialization`, or `us-13.10-capability-matrix` for a single large story). Build the whole phase there.
- **Small follow-up fixes go straight to `main`.** A copy fix, a stale test, a one-line guard — a branch would cost more than it protects. Use judgement: if it touches a migration or changes behavior across several files, branch it.
- **Testing and the user's manual UAT happen before the merge to `main`**, on the branch. A story reaching `Testing` is not permission to merge — only the user moves a story past `Testing`.
- **Merge to `main` once UAT passes.** `main` is the integration branch and the source of truth for what is built.
- **`prod` only ever receives merges from `main`.** A release is a PR `main` → `prod`; merging it fires `.github/workflows/deploy-prod.yml`, which rsyncs to the GCP VM, builds, restarts `factory-web`/`factory-api`, and health-checks all three services.
- **Release PRs merge with a merge commit — never squash or rebase.** A merge commit keeps every `main` commit reachable from `prod`, so the drift check below stays meaningful. Squashing mints a new commit that exists only on `prod`, which is indistinguishable from someone having worked there.

**Never commit to `prod`, and never branch from it.** `prod` must never contain work that is not already on `main` — if it does, `main` has silently lost a fix and the next release will revert it. This has happened: `2a80522` (`ci: fix apostrophe breaking the deploy SSH command quoting`) was authored directly on `prod` and only reached `main` because someone re-applied it by hand.

Before releasing, verify `prod` is not ahead:

```bash
git fetch origin
git rev-list --count --no-merges origin/main..origin/prod   # must be 0
```

Only non-merge commits count: release merge commits legitimately live on `prod` alone. A non-zero result means someone either worked on `prod` directly or squash-merged a release. Port those commits back to `main` first (`git merge origin/prod` on `main` — a no-op when the trees already agree) — do not resolve it by force-pushing `prod`.

Hotfixes are not an exception: fix on `main` (or a branch off it), then release. The deploy is ~3 minutes.

## Conventions & gotchas

- **Migrations**: files in `infra/supabase/migrations/` (numbered `NNN_name.sql`) must also be **applied to both live Supabase projects** (prod and dev) in the same change, then regenerate the types in `apps/web/src/lib/supabase/database.types.ts` and let `test_embed_ambiguity.py` sweep the embeds. `python scripts/migrate.py apply NNN_name.sql` does all four steps; `python scripts/migrate.py drift` compares function bodies across the two projects when they're suspected of diverging. A written-but-unapplied migration makes correct code look broken.
- **RLS from the first migration**: every table is org-scoped with RLS; use the `public.is_org_member(org)` helper in policies. Verify cross-org isolation for new tables.
- **Secrets are write-only**: API keys go in Supabase Vault via `security definer` RPCs (see migration 002 / `set_llm_api_key`); the client may see at most a `key_last4`. Never add a policy or view that returns key material. **Server credentials** (SSH passwords/private keys, us-1.28) live in a *second* write-only location — the private `data` Storage bucket, per-org folders `<org_id>/servers/<server_id>/`, readable by `api` (service role) only. That bucket has **no** `storage.objects` policies, so RLS default-deny blocks all client read/list/download; never add one. Secrets flow browser → `api` → Storage; no endpoint, response, log, or signed URL echoes them back (the UI shows at most "Key set · <fingerprint>").
- **"Build less API"**: plain CRUD goes through the Supabase JS SDK under RLS directly from `apps/web`. FastAPI (when it lands, us-1.8+) is only for orchestration: dispatch, runner callbacks, GitHub operations, LLM calls.
- **Name the relationship in a PostgREST embed** when the two tables are reachable more than one way — `projects!deployments_project_id_org_id_fkey(name)`, not `projects(name)`. A table holding NOT NULL foreign keys to two others is a junction to PostgREST, so it silently adds a second path between them and *every* un-hinted embed of that pair starts answering `300 Multiple Choices` (PGRST201). That is how a delete button came to answer 500 (BUG-1.1). Adding such a table breaks existing queries, not just new ones: `apps/api/tests/test_embed_ambiguity.py` derives the graph from `database.types.ts` and checks every embed in the repo, so regenerate the types with the migration and let it tell you.
- **Web divergences (Next 16, Base UI, Tailwind v4)**: the cheatsheet is `apps/web/AGENTS.md` — read it before web work; it covers `proxy.ts`, `render=` composition, `Select items`, tokens, and the shared components, so you rarely need `node_modules/next/dist/docs/`.
- **Never commit secrets**: `supabase.txt` and `.env.local` are git-ignored; keys shown in the repo's history once already forced a rotation warning.

<!-- buildmill:instructions:start -->

## Versioning & Release

How this project versions and ships. The factory computes the version — you
never hand-pick one mid-flight.

### Version scheme

A release is versioned **`YYYY.MM.DD.N`** by default: the date it was cut plus
a same-day counter. The manager may override the proposal when cutting, and
from that moment the version is fixed — an agent reads it off the release and
never changes one.

**us-100.6 changed who proposes it.** A project can now write its own
versioning rules in its Agent Instructions, and the release agent reads them
and proposes a version with its reasoning. The older rule — *"the factory
computes the version; you never hand-pick one"* — no longer holds for the
proposal, only for what happens after the cut. Three things still do:

- the manager's override at cut is final, and the version is immutable after;
- a project whose Agent Instructions say nothing about versioning gets
  `YYYY.MM.DD.N`, unchanged;
- a proposal that collides with an existing version, or could not be a git
  tag, is refused and the computed version is used instead. The factory never
  ships a release with no version because an agent had an opinion.

### What a release is

One build, cut from the default branch and **pinned to a commit**. Everything
downstream — the notes, the UAT deployment, the promotion to production —
uses that pinned commit, never the branch head at the time it runs.

Work items are **not** linked to releases. A work item is complete when it
merges; which release carried it is a fact recorded on the release.

### The path

1. **Cut** — pins the commit, snapshots the work items merged since the last
   released version, and tags it.
2. **UAT** — an agent writes the release notes from the real commit range,
   deploys the pinned commit to the designated UAT deployment, and verifies
   its health. Every release goes to UAT first; it is not a choice.
3. **Test** — the release carries the included work items' test cases plus
   regression cases the agent authored. A human runs them.
4. **Sign-off** — allowed only when the UAT deployment succeeded *and* every
   case passed. Blocked counts as not passed.
5. **Promote** — ships the same pinned build to production. Promotion never
   re-versions and is never automatic.

A release is **immutable**. If UAT fails, the release is rejected and a new
one supersedes it — a version name means exactly one build, forever.

## Working with Build Mill

This project is developed through Build Mill — an AI software factory where a human manager approves every gate and AI workers do the drafting, planning, and coding.

### Work items

Every change starts as a typed work item: a feature, bug, chore, or story. A feature first gets a PRD (problem, goals, out of scope, acceptance criteria) that the manager approves before any engineering, then splits into child stories. Bugs, chores, and standalone stories skip the PRD and go straight to planning.

### How work reaches a worker

Each item is dispatched in two phases: a plan run (write an implementation plan and a test plan — the manager approves both before any code), then a code run (implement the approved plan). Dispatched runs wait in a pool; an authorized worker — the autonomous runner or a person's IDE agent connected over MCP — claims first-come-first-served, holds a lease, and extends it while working.

### Getting the code and handing it back

Two transports feed one review pipeline — pick per worker:

- **Agents — MCP only (the default for AI workers):** no git tooling needed. `get_workspace` downloads the working tree as a zip pinned to a base commit; work on it locally; `submit_changeset` hands the changed files back and the factory builds the commit, pushes the work branch, and opens the PR itself. A stale base answers the current head — refetch and reapply; nothing is ever overwritten.
- **Git-native workers (humans in IDEs, or repositories above the snapshot ceiling):** clone and push through the factory's own git remote using the worker token as the HTTP Basic password — no GitHub credentials and no manual pull request. Work on the branch named in the run context, push it, then `submit_code_work`; the factory opens the PR itself.

### Context every run carries

The run context bundles the story and acceptance criteria, the governing PRD, the approved plan on code runs, these guidelines, project learnings, attached documents, and — on retries — the earlier rejection feedback. Each work item also carries a living instruction set (readable over MCP via get_instructions, no claim needed) and a comment thread shared between the manager and workers (post with add_comment).

### Review and completion

Submitted work lands in the manager's review: approve merges the PR; send back returns it with a comment, and the retry run carries that feedback. When a feature's last story merges, the feature completes automatically. Releases and deployments are manager-triggered, release-style with rollback.

### Ground rules for workers

- Honor the approved plan and the acceptance criteria; keep diffs focused.
- Plan runs never modify project files; code runs never open PRs or create branches beyond the named one.
- Every gate decision and state change is audited — work transparently, submit honestly.

## Commands

From the repository root (`package.json`):

```bash
npm install        # workspace install (web + public)
npm run dev        # web dev server → http://localhost:3000  (next dev)
npm run build      # production build of web; `next build` also type-checks — run before committing web changes
npm run lint       # eslint, via apps/web
npm run start      # next start (production web)
npm run public     # serve the static marketing site
```

**Python apps are not covered by the root npm scripts and must be run directly.** The root `api` and `test:api` scripts hard-code **Windows** venv paths (`apps\api\.venv\Scripts\python`) and only work on Windows. On Linux/macOS/CI, create the venv and invoke the tools yourself:

```bash
cd apps/api
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m uvicorn app.main:app --app-dir . --reload --port 8000   # run the API
python -m pytest                                                 # run the API tests
```

The runner runs with `python -m supervisor` from `apps/runner/` (see `apps/runner/README.md`).

## Conventions & code style

- **This is not the Next.js in your training data.** Next 16 replaces `middleware.ts` with `src/proxy.ts` (an exported `proxy` function — see `apps/web/src/proxy.ts`). Read `node_modules/next/dist/docs/` before assuming App Router APIs match memory (`apps/web/AGENTS.md`).
- **shadcn/ui here is Base UI, not Radix.** Components live in `apps/web/src/components/ui/`; triggers compose with `render={<Button />}` rather than `asChild` (see `dialog.tsx`). Reuse the shared `StatusBadge` (`status-badge.tsx`) and `EmptyState` (`empty-state.tsx`) instead of inventing per-page patterns.
- **Migrations are a two-step change.** A file added to `infra/supabase/migrations/` (`NNN_name.sql`) must also be applied to the live Supabase project in the same change, and then `apps/web/src/lib/supabase/database.types.ts` regenerated. A written-but-unapplied migration makes correct code look broken.

## Overview & stack

Software Factory ("Build Mill") is an AI-driven software-delivery pipeline with a human manager in the loop: define a story, an AI worker turns it into a PR, the manager reviews and merges. The system of record is **hosted Supabase** (Postgres + Auth + Storage + Realtime); the backend is a deliberately thin FastAPI service reserved for orchestration. See `ARCHITECTURE.md` for how the pieces fit and `APPLICATION.md` for the authoritative catalog of surfaces and rules.

It is a monorepo of four apps under `apps/`:

- **web** — Next.js 16 (`next@16.2.10`), React 19, TypeScript 5, Tailwind v4, and shadcn/ui built on **Base UI** (`@base-ui/react`), talking to Supabase through `@supabase/ssr` and `@supabase/supabase-js`; `@xterm/xterm` powers the in-app terminal. (`apps/web/package.json`)
- **api** — Python FastAPI: `fastapi`, `uvicorn`, `pydantic` v2 / `pydantic-settings`, `PyJWT[crypto]`, `httpx`, `psycopg[binary]` v3, `litellm`, `paramiko`, `mcp`, `pathspec`. (`apps/api/requirements.txt`)
- **runner** — the operator-side supervisor worker (`python -m supervisor` from `apps/runner/`); Python with `httpx` + `websockets` and a persistent control socket to `api`. (`apps/runner/requirements.txt`, `apps/runner/README.md`)
- **public** — a zero-dependency static marketing site served by a small Node `server.js`. (`apps/public/`)

Node `>=20` is required (`package.json` `engines`). The browser talks to Supabase directly for CRUD under RLS and calls `api` only for orchestration — dispatch, runner callbacks, GitHub App operations, LLM routing, and the SSH/SFTP bridge to registered servers.

## Testing

**Two suites, not one (US-80.1).** The api suite used to take ~30 minutes, which is
long enough that it gets skipped — and this project has already paid for that: a
console shipped that had never worked, because the test that would have caught it
faked the database call and nobody was re-running half an hour to find out.

- **Essential** — the default, and the gate after coding: `cd apps/api && python -m pytest`
  (or `npm run test:api`). **~30 seconds**, ~1540 tests. "Tests pass" in a commit
  message means this, unless the message says otherwise.
- **Full QA** — everything: `python -m pytest --full` (or `npm run test:api:full`).
  ~30 minutes, because it talks to a real database. Run it before a release, when
  touching migrations, RPCs or RLS, and whenever you want the whole thing.

**Essential blocks outbound network, and that is the whole speedup.** The suite was
never slow because of heavy computation — `settings_override` points PostgREST at
`https://test.supabase.co`, and a route test whose read is not faked *called it for
real* and waited on it before failing into the refusal the test asserts. So an
autouse fixture makes outbound name resolution fail instantly. If you see

    socket.gaierror: outbound network blocked in tests (host=...)

your test is reaching the network: **fake the read** (that is what the failure is
telling you, and it is almost always the right fix), or mark it `needs_db` if it
genuinely requires Postgres.

What Essential holds back, and nothing else: `*_sql.py` (by filename), anything
marked `needs_db` (`test_factory_mcp.py`, `test_git_proxy_integration.py` — they
need a database and their names do not say so), and anything marked `slow` (the
escape hatch for something measured expensive; nothing carries it today). The
skipped count is printed, so you always know what was held. `test_suite_split.py`
pins all of this — including that the guard is still live, since if it silently
stops working the suite quietly goes back to half an hour.

Three separate test suites; there is no single command that runs them all.

- **web** — `npm run test:web` runs Node's built-in test runner over `apps/web/src/**/*.test.ts` (`node --experimental-strip-types --test`). Coverage is currently sparse, so a passing web run proves very little; add tests alongside behavior you change.
- **api** — `pytest`, configured by `apps/api/pytest.ini` (`testpaths = tests`, `-q`) and split by `tests/conftest.py`. Run it with `cd apps/api && python -m pytest` (the root `test:api` script is Windows-only — see Commands). Two flavors:
  - **Unit/route tests** build a FastAPI `TestClient` against fake settings and a synthetic JWT keypair — no database, no network. These are Essential.
  - **`*_sql.py` tests** connect to a real Postgres via `DATABASE_URL` (env, or read from `apps/api/.env`) to exercise RPCs, RLS and indexes; each rolls back so nothing is left behind. These are Full QA. They also `pytest.skip` when `DATABASE_URL` is unset — so on a machine without the credential, `--full` and Essential cover the same ground and the SQL/RLS layer is untested either way. Set it when your change touches migrations or policies.
- **runner** — `npm run test:runner` (or `cd apps/runner && python -m pytest`); ~80 seconds, no database, not split.

## Monorepo layout

```
software-factory/
├── apps/
│   ├── web/      # Next.js (App Router) — npm workspace
│   ├── api/      # FastAPI backend — Python, pip + venv (not an npm workspace)
│   ├── runner/   # Operator-side supervisor worker — Python (not an npm workspace)
│   └── public/   # Static marketing site — npm workspace
├── infra/supabase/migrations/   # numbered SQL migrations, NNN_name.sql
├── stories/      # user stories, one file per story
├── docs/         # architecture & design docs (incl. docs/factory/)
└── scripts/, tools/   # one-off Python helpers
```

The root `package.json` declares npm workspaces for **`apps/web`** and **`apps/public`** only; its scripts delegate to `apps/web` with `--workspace`. **`apps/api`** and **`apps/runner`** are Python projects managed with their own `requirements.txt` and a local `.venv` — they are not wired into the npm workspace, so root `npm` scripts do not build or test them. Database migrations live in `infra/supabase/migrations/` as `NNN_name.sql`, applied in numeric order (180+ and counting).

## Security & trust boundaries

- **RLS from migration 001.** Every table is org-scoped; policies use the `public.is_org_member(org)` helper (defined in `001_initial.sql`, used throughout the migrations). Verify cross-org isolation for any new table.
- **Secrets are write-only, in two places.** LLM/provider keys go into Supabase **Vault** via `security definer` RPCs (e.g. `set_llm_api_key`); the client sees at most a `key_last4`. Server credentials (SSH keys/passwords) live in the private `data` Storage bucket under per-org `<org>/servers/<server>/` paths (`apps/api/app/storage.py`), readable only by `api` (service role) — that bucket has **no** `storage.objects` policies, so default-deny blocks all client access. Never add a view, policy, endpoint, log, or signed URL that returns key material. `.env`, `.env.local`, and `supabase.txt` are git-ignored (`.gitignore`) — never commit secrets.
- **Build less API.** Plain CRUD goes browser → Supabase JS SDK under RLS. FastAPI is only for orchestration: dispatch, runner callbacks, GitHub ops, LLM routing, and the SSH/SFTP bridge (`ARCHITECTURE.md`). Don't re-wrap CRUD that Supabase already exposes.

## Deployment and Release

Code flows one way only: **branch → `main` → `prod`**, and there are exactly two deploy pipelines — prod and its UAT mirror. (The version *scheme* and release lifecycle — cut, UAT, sign-off, promote — are covered in Versioning & Release; this section is the concrete plumbing.)

**The pipeline.** `.github/workflows/deploy-prod.yml` fires on a push to the `prod` branch (i.e. a release PR `main → prod` merging) or manual `workflow_dispatch`. It has a `deploy-prod` concurrency group with `cancel-in-progress: false`, so deploys queue rather than clobber each other. `.github/workflows/deploy-uat.yml` mirrors it for the `uat` branch: same VM, deploying to `/opt/uat.buildmill.dev`, restarting `factory-web-uat`/`factory-api-uat` on ports 3051/3061. There is **no** separate CI/test workflow in `.github/workflows/` — tests are not gated in CI today.

**What it does.** Over SSH to the GCP VM, it `rsync`s the source (excluding `.git`, `.github`, `node_modules`, `.next`, `.env*`, `.venv`) to `/opt/buildmill.dev`, then remotely runs `npm install`, `npm run build`, `pip install -r apps/api/requirements.txt`, and `sudo systemctl restart factory-web factory-api` (with a best-effort restart of `factory-public`). It then health-checks, polling up to ~60s each: web on `127.0.0.1:3050`, api on `:3060/docs`, public on `:3040`; any failure fails the deploy.

**Where it runs** (`GCP-setup.md`). The host is `gcp-vm-rebee`; the app is stateless (Supabase is fully hosted). Ingress is a **Cloudflare Tunnel** (`cloudflared`), not nginx — public hostnames map to local ports in the Cloudflare Zero Trust dashboard, nothing on the VM routes: `app.buildmill.dev`→3050, `api.buildmill.dev`→3060, `buildmill.dev`→3040. The old `factory.nexdb.cloud` / `factory-api.nexdb.cloud` hostnames **no longer resolve** and are stale wherever they still appear; the install directory is `/opt/buildmill.dev` (renamed from `/opt/factory.nexdb.cloud` on 2026-07-29). The **runner is deliberately not deployed to GCP** — it runs on the operator's own machine per the trust boundary.

**Branch discipline** (`CLAUDE.md`). Never commit to `prod` and never branch from it; `prod` must never be ahead of `main`. Release PRs merge with a **merge commit** — never squash or rebase — so every `main` commit stays reachable from `prod`. Before releasing, confirm no drift with `git rev-list --count --no-merges origin/main..origin/prod` (must be `0`); a non-zero result means a fix was authored on `prod` and must be ported back to `main` first, not force-pushed away.

## Factory documentation tree

The project's requirements live in this repository under `docs/factory/`.

**It is already on disk.** A code run receives the repo as a workspace
pinned to a commit, so this is a local directory — read it with your normal
file tools. No `get_repo_tree` or `read_repo_file` call is needed.

**Addressing.** Paths are work-item ids, never titles, so a link written
today still resolves after somebody rewords a title:

```
docs/factory/index.json        every item, in build order
docs/factory/INDEX.md          the same list, for humans
docs/factory/us-4.1/prd.md     a feature's approved PRD
docs/factory/us-4.1/us-4.2.md  a story in that feature
```

**Read `index.json` first.** One read answers what exists, in what order,
and where — instead of walking the tree and parsing prose. Each entry, and
the YAML front matter at the top of every `.md` file, carries:

| key | meaning |
| --- | --- |
| `id` | the work-item display id, e.g. `US-4.2` |
| `issue_id` | the uuid, for MCP calls |
| `type` | `story` or `feature` |
| `title` | the title as approved |
| `parent` | the feature this story belongs to; `null` for a feature |
| `epic` | the epic number |
| `order` | position in build order |
| `has_plan` | an approved implementation plan is in this file |
| `has_test_plan` | an approved test plan is in this file |
| `merge_commit` | the commit that shipped it, or `null` |
| `generated_at` | when this tree was written |

A story with `has_plan: false` has been dispatched but not planned — it
carries the requirement only. It is still worth reading: it tells you what
is coming and what is not yours to build.

**Before you design, read the stories that precede yours in the same
feature.** Their approved plans and their `## Outcome` sections say what
was already decided and what actually shipped, so you extend that shape
instead of inventing a competing one.

**Build Mill owns this tree.** It is regenerated wholesale from approved
state and never read back, so edits made here are overwritten and lost —
change the source of truth in Build Mill instead.

<!-- buildmill:instructions:end -->
