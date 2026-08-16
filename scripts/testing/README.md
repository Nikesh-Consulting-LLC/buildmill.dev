# API test suite

Playwright tests for the whole Build Mill FastAPI service — **227 operations
across 26 routers**, one spec file per category, plus a runner that folds the
result into a single summary table.

> **The catalog can go stale.** `endpoints.json` was generated on 2026-08-15.
> The auth-boundary layer covers exactly what it knows, so an endpoint added
> since is **not** tested until it is regenerated:
>
> ```bash
> python tools/generate-catalog.py        # against a running app
> ```
>
> Known missing at the time of committing: Phase 103's
> `GET /api/v1/worker/release-prep/held`.

No browser is involved. Every test uses Playwright's `request` fixture, so
`npx playwright install` is never needed and nothing downloads a Chromium.

```bash
cd scripts/testing
npm install
node run-all.mjs
```

---

## What it actually tests

Three layers, in decreasing order of how safely they can be pointed at a live
deployment.

**1. The auth boundary — generated, all 227 operations, always runs.**
`lib/catalog.ts` reads `endpoints.json`, which is generated from the running app
by `tools/generate-catalog.py`. Each category spec calls
`describeAuthBoundary("<tag>")` and gets one test per operation asserting two
things: no credential is refused with 401, and a *forged credential of the right
shape* is refused too. The second half is the one that earns its keep — a guard
that merely checks a header is present, or trusts an unverified JWT, passes the
first check and fails this one.

These tests are safe anywhere. Every request is refused before the handler runs,
so nothing is dispatched, deployed, merged or deleted.

**2. Contract and refusal tests — hand-written per category, always runs.**
An unknown id, a body the schema rejects, a path-traversal attempt: each must
answer a legible 4xx with a `detail` the web app can render, never a 500.
`security.spec.ts` adds the cross-cutting rules — a 422 that never echoes the
input it rejected (a request body can carry a pasted PAT or an SSH key), CORS as
an allow-list everywhere except the public report endpoint, and a sweep asserting
no operation in the api answers 2xx to an anonymous caller.

**3. Read and mutation tests — opt-in.**
Reads switch on when you supply a credential and the relevant id. Mutations stay
off until `ALLOW_MUTATIONS=1`, because this api creates and deletes orgs,
dispatches agent runs, merges PRs and deploys to real servers over SSH.

A missing credential **skips**, it never fails — and the runner prints what was
skipped and which variable would switch it on, so a green run with skips is
never mistaken for full coverage.

## Categories

One file per router tag. `node run-all.mjs --list` prints them; pass any subset
as arguments to run only those.

| Spec | Covers | Ops |
| --- | --- | ---: |
| `admin` | platform console: orgs, users, memberships, templates, run config | 39 |
| `agents` | agent-servers, agent-pools, agents, agent-sessions | 20 |
| `app-issues` | the public `POST /report/{id}/issues` ingestion endpoint | 1 |
| `auth` | `GET /auth/me` and the JWT gate 203 operations sit behind | 1 |
| `deployments` | run, promote, rollback, drift, zip, preflight, env | 16 |
| `github` | App install callback, PAT connections, repos, branches | 10 |
| `gitproxy` | the factory git remote at `/git/*` | 3 |
| `health` | health, build stamp, OpenAPI, 404/405 shapes | 1 |
| `issues` | dispatch, batch dispatch, attempts, revert | 9 |
| `llm` | routing, spend, prices, and the runner LLM gateway | 14 |
| `mcp` | MCP catalog, the per-run scoped proxy, the `/mcp` server | 6 |
| `members` | org member provisioning and password reset | 2 |
| `notifications` | notification endpoints and their test-send | 3 |
| `presets` | org presets and platform preset templates | 8 |
| `projects` | guidelines, learnings, instructions, build config, releases | 15 |
| `releases` | sign-off, promote, reject, retry, rollback, suites | 12 |
| `reviews` | the manager's decisions on a submitted run | 10 |
| `runner` | supervisor runner config, policy preview, commands | 6 |
| `security` | cross-cutting rules and the suite's own coverage meta-test | — |
| `servers` | registered servers and the SFTP bridge | 18 |
| `worker` | the claim contract every AI worker speaks | 15 |
| `workflow` | PRD, elaboration, plan, breakdown, replan, wireframes | 18 |

**WebSockets are out of scope.** The runner control socket
(`/api/v1/runner/socket`) and the run console are not driven here; Playwright's
`request` fixture is HTTP-only.

## Configuration

Copy `env.example` to `.env` and fill in what you have. Nothing is required —
with an empty file the suite still runs the whole unauthenticated half.

```bash
cp env.example .env
```

The credential that buys the most coverage is an ordinary member
(`TEST_USER_EMAIL` / `TEST_USER_PASSWORD`): it unlocks ~95 tests. A platform
admin unlocks the admin console reads, and `TEST_WORKER_TOKEN` unlocks the pool.

`SUPABASE_URL` must be the **same project the target api verifies against**
(dev = `build-mill-dev`, prod = `Software-Factory`), or every token minted here
is correctly rejected as forged.

Sign-in happens once per run in `lib/global-setup.ts`, not once per worker
process, and the tokens are cached in `.auth/` (git-ignored).

## The runner

```bash
node run-all.mjs                    # every category
node run-all.mjs admin worker       # only those
node run-all.mjs --list             # what exists
node run-all.mjs --base-url=https://uat-api.buildmill.dev
node run-all.mjs --workers=2        # throttle concurrency
node run-all.mjs --mutations        # allow state-changing tests
```

It drives `playwright test` with the JSON reporter, groups the result by spec
file, and prints a per-category table plus every failure in full and the skips
grouped by reason. It also writes `results/summary.md` and
`results/summary.json` (both git-ignored). Exit code is 0 only when nothing
failed; skips do not fail a run.

```
  CATEGORY       TOTAL   PASS   FAIL   SKIP      TIME
  ───────────────────────────────────────────────────
  admin             47     39      0      8      6.4s
  agents            29     20      0      9      2.4s
  ...
  TOTAL            377    234     29    114    635.5s
```

`playwright test` still works directly if you want its own reporters, watch mode
or `--grep`; the runner is a summary layer, not a wrapper you are stuck with.

## Keeping the catalog honest

`endpoints.json` is generated, never hand-edited. Regenerate it whenever a router
changes:

```bash
apps/api/.venv/Scripts/python scripts/testing/tools/generate-catalog.py
```

(`apps/api/.venv/bin/python` on POSIX. It imports `app.main`, so it needs
`SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY` set — the script supplies
placeholders when they are absent, and nothing is contacted at import time.)

Two tests keep it from silently rotting:

- **`security › the catalog matches the api the tests are pointed at`** fetches
  the target's own `/openapi.json` and fails if it serves an operation the
  catalog does not know. Without it, editing a router and forgetting to
  regenerate would leave the suite testing yesterday's api — and passing.
- **`security › every router tag is owned by a category spec`** fails when a new
  router tag appears that no spec claims, so a new surface gets an owner rather
  than only the blanket sweep.

## Pointing it at something

**Local.** `npm run api` from the repo root, then `node run-all.mjs`. Note that
a local api with no `DATABASE_URL` cannot resolve worker tokens, report keys,
gateway keys or MCP keys — those refusals need Postgres, and without it they
answer 500 instead of 401. The suite reports that faithfully (~29 failures, all
in `worker`, `gitproxy`, `llm`, `mcp` and `app-issues`), which is correct
behaviour for that environment and not a product defect: `apps/api/tests`
covers the same paths with the database faked and asserts 401.

**UAT.** The natural home for a full run, `--mutations` included.

**Production.** Read-only credentials, and leave `ALLOW_MUTATIONS` off. The
unauthenticated layers are safe there by construction; the read layer is as safe
as the account you sign in with.
