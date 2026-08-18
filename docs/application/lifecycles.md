_Part of the [application reference](../../APPLICATION.md) — the index, audience guide, and rules & invariants live there. Keep this file current in the same commit as the change it describes._

## Lifecycles

Every object in the previous section that carries a `status` column has a real Postgres check
constraint enumerating its legal values — that constraint is the vocabulary; nothing below adds
or removes a value it doesn't allow. What a check constraint does *not* guarantee is that every
value it permits is reachable by a code path a manager or worker can actually invoke — where no
such path was found, the row says so rather than inventing one.

### Issue status

`issues.status` (`infra/supabase/migrations/031_issues.sql:56-60`, unchanged since) allows:
`draft`, `prd-review`, `ready`, `planning`, `plan-review`, `planned`, `queued`, `running`,
`needs-fixes`, `in-review`, `merged`, `failed`, `done`.

| From | To | Trigger | Who can | Side effect |
|---|---|---|---|---|
| `draft` / `ready` / `failed` | `queued` | Manager dispatches (`POST /issues/{id}/dispatch` → `dispatch_issue`) | Manager (web), any org member's JWT — not exposed over Worker MCP | A `plan`-kind run is created and queued. us-96.1: a **chore** gets a `code`-kind run instead — a chore has no planning phase (naming `plan` is refused: "a chore has no planning phase — dispatch builds it"), its context carries no `plan`/`test_plan` keys, and its instructions resolve to the `chore` kind via `instruction_kind_for`. us-96.4: under `route_feature_as_one` (US-86.1's switch 2) a feature child that has **never been planned** (no plan artifact in any state) refuses individual plan dispatch — "FEAT-x.y owns the plan — dispatch the feature to plan all N stories"; `dispatch_feature_batch` is exempt via the transaction-local `factory.feature_batch` flag and also plans late-arrival stories added after their siblings were planned. Remediation (`failed`/`needs-fixes`) and revision (any existing plan artifact) stay individually dispatchable. US-22.10: a `planned` → code dispatch is **refused** when the project is in `feature`/`epic` mode and the story has a parent feature — unless the story is `failed`/`needs-fixes`, the trouble exemption that keeps a stuck batch recoverable. Sequential build mode (migration 230, `projects.sequential_only`, default `true`): dispatch of **any kind** is refused outright — before a run is even created — while another non-abandoned issue in the same project sits in `planning`/`plan-review`/`planned`/`queued`/`running`/`needs-fixes`/`in-review`/`failed`. Composes with `build_mode`; toggled per project in Task Processing settings |
| `planned` / `needs-fixes` (with an approved plan artifact) | `queued` | Same dispatch endpoint | Manager (web) | A `code`-kind run is created and queued |
| `needs-fixes` (no approved plan artifact) | `queued` | Same dispatch endpoint | Manager (web) | A `plan`-kind run is created instead — a code rejection with no surviving plan re-plans first. Chores exempt (us-96.1): always another `code` run carrying the rejection feedback |
| `failed` | `queued` | Same dispatch endpoint | Manager (web) | Always a `plan`-kind run, even if the failed run was a `code` run — `dispatch_issue` treats `failed` as needing a fresh plan regardless of what failed. Chores exempt (us-96.1): always another `code` run |
| `queued` | `planning` | A worker claims a `plan`-kind run (`claim_work`) | Worker (MCP) | The run moves `queued` → `running`; the issue reflects "someone is planning it" |
| `queued` | `running` | A worker claims a `code`-kind run (`claim_work`) | Worker (MCP) | The run moves `queued` → `running` |
| `planning` | `plan-review` | Worker hands back the plan (`submit_plan`) | Worker (MCP) | Run → `succeeded`; `plan`/`test_plan` artifacts stored as `draft` |
| `running` (code) | `in-review` | Worker hands back code (`submit_code_work` / `submit_changeset`) on a project whose branching strategy opens a PR (`story` or `work_item`) | Worker (MCP) | Run → `succeeded`; the factory verifies the branch, opens/finds the PR, pulls the diff |
| `running` (code) | `merged` | Same submit calls, but the project's branching strategy is `main`, which commits straight to the default branch (`submit_mode == 'direct'`) | Worker (MCP) | Run → `succeeded`; no PR — the review gate is bypassed entirely |
| `planning` / `running` | `failed` | Runner submits with `error` set — `POST /worker/runs/{run_id}/submit`, the `Submit` body's `error` field; not a `submit_*` MCP tool call | Runner (HTTP) | Run → `failed`; for `code` runs a synced GitHub issue is closed. us-96.6: `breakdown`-kind runs join `prd`/`test`/`elaborate` in the exemption — the run fails, the feature **stays `ready`** and its breakdown panel shows the error beside the retry |
| `planning` / `running` | `queued` | Worker releases the claim (`release_work`), or the lease expires unattended | Worker (MCP) / system (expiry sweep — API startup, before every pool listing, and a 60-second timer since US-13.6) | Run → `queued`, `worker_id` cleared; issue forced to `queued` **for `plan`/`code` claims only** (US-13.6 kind-guard); a `claim-expired` event names the worker and how long it held the claim |
| `plan-review` | `planned` | Manager approves the plan (`POST /issues/{id}/plan/approve`) | Manager (web) | Plan/test-plan artifacts → `approved`; the test plan's cases materialize into `test_cases` |
| `plan-review` | `draft` / `ready` / `failed` / `planned` | Manager sends the plan back with a comment (`POST /issues/{id}/plan/send-back`) | Manager (web) | Returns to whatever status the issue had when this plan run was dispatched (recorded on the `plan-dispatched` event); falls back to `draft` if that value isn't one of these four |
| `prd-review` | `ready` | Manager approves the PRD, feature-only (`POST /issues/{id}/prd/approve`) | Manager (web) | PRD artifact → `approved`; `breakdown_mode`/`breakdown_instructions` saved on the issue |
| `prd-review` | `prd-review` (unchanged) | Manager sends the PRD back with a comment (`POST /issues/{id}/prd/send-back`) | Manager (web) | Prior draft PRD artifact → `superseded`; a fresh `prd` run is queued carrying the comment as feedback |
| `in-review` | `merged` | Manager approves the run, code runs only (`POST /runs/{id}/approve` → `approve_run`) | Manager (web) | PR merged on GitHub; an `approvals` row (`code-review`, `approved`) is written; if this was the last open child of a parent feature, the **parent** auto-completes to `done` |
| `in-review` | `needs-fixes` | Manager rejects the run with a required comment (`POST /runs/{id}/reject` → `reject_run`) | Manager (web) | An `approvals` row (`code-review`, `rejected`) is written |
| (feature) any open status | `done` | Automatic: the last open child of a parent feature reaches `merged` | System, inside `approve_run` | No manager action on the parent — its status flips as a side effect of a child's approval |

A `prd` or `breakdown`-kind run is different from `plan`/`code`: dispatching one
(`dispatch_prd_draft`, `dispatch_breakdown`) never writes `issues.status`, and claiming one
(`claim_work`) doesn't either — only `plan`- and `code`-kind claims advance the issue's status.
The issue sits at whatever it already was (`draft`/`prd-review`/`ready`) while the run itself
goes `queued` → `running` → `succeeded`/`failed`. A successful `submit_prd` moves the issue to
`prd-review`; a successful `submit_stories` (breakdown) leaves a `ready` feature at `ready` and
creates its children as `draft` stories.

#### Known gaps (verified 2026-07-19)

Things found in code worth flagging rather than silently encoding as normal. (A third gap
documented here previously — releasing or losing a `prd`/`breakdown` claim forcing the issue to
`queued` — was fixed by US-13.6: `release_claim`, the lease-expiry sweep, and `force_requeue_run`
now touch `issues.status` only for `plan`/`code` kinds.)

- **A `code` run merged via the `direct` strategy does not auto-complete its parent feature.**
  The "last child merged → parent goes `done`" side effect lives entirely inside the
  `approve_run` Postgres function, which only the manager's `/runs/{id}/approve` review-gate path
  calls. A direct-strategy merge reaches `db.complete_run`'s own `direct` branch instead, which
  sets the issue straight to `merged` without ever calling `approve_run` — so a feature whose
  every child merged this way never receives the `done` status.
- ~~A breakdown-run failure leaves no code path back to a dispatchable state.~~ **Fixed by
  us-96.6** (2026-08-15): a failed breakdown leaves the feature at `ready` (the same exemption
  `prd` runs had), the orphan reaper stops forcing `prd`/`breakdown`/`test`/`elaborate` issues to
  `failed`, and migration 260 repaired any feature the old behavior had stranded (zero found on
  either live project at apply time).

### Run outcome

`runs.status` allows: `queued`, `running`, `succeeded`, `failed`, and — since migration 145
(US-27.10) — `cancelled`. A cancelled run is terminal and is **not** a failure: it never ran,
nothing is wrong with the machine, and it must never colour a worker's health or the incident
feed. A queued run is cancelled outright; a running one gets Phase 15's cooperative stop and
lands `cancelled` when its worker hands back. It carries `cancel_reason` (required) and
`cancelled_at` — deliberately **not** `finished_at`, because `activity_feed`'s run-finished
row renders anything that did not succeed as "<kind> run failed".

`runs.kind` (widened through migration 185) allows: `plan`, `code`, `prd`, `breakdown`, plus the
Phase 13 kinds — `test` (US-13.11: staffed verification over a submitted code run's branch;
claim/completion/failure never touch issue status), `release` (US-13.12), and `deploy`
(US-13.13). Since US-13.12 every run carries a NOT NULL `project_id`; `issue_id` is nullable for
the project-scoped kinds only (`check (issue_id is not null or kind in ('release','deploy'))`),
and `deployment_id` names a deploy run's exact deployment definition. `last_heartbeat_at`
(US-13.6) records when the claiming worker last spoke; `handback_notes` (US-13.3) carries what
the agent wanted the manager to know at hand-back.

| From | To | Trigger | Who can | Side effect |
|---|---|---|---|---|
| `queued` | `running` | `claim_work` | Worker (MCP) | `worker_id` set, lease started; issue status advances for `plan`/`code` kinds only |
| `running` | `queued` | `release_work`, or lease expiry | Worker (MCP) / system (expiry sweep) | `worker_id` cleared |
| `running` | `succeeded` | The matching `submit_*` call with no `error` (`submit_plan`, `submit_prd`, `submit_stories`, `submit_code_work`, `submit_changeset`) | Worker (MCP) | Kind-specific: artifacts stored, PR opened, or child stories created |
| `running` | `failed` | `POST /worker/runs/{run_id}/submit` with the `Submit` body's `error` field set — none of the five `submit_*` MCP tools takes an `error` parameter; the runner (`apps/runner/runner.py`, `apps/runner/supervisor/workloop.py`) calls this HTTP endpoint directly on failure | Runner (HTTP) | Issue → `failed` (except `prd`-kind runs, whose issue status is left untouched) |
| `running` (no worker) | `failed` | Legacy/orphaned run found at API startup with no claiming worker (`reap_orphaned_provider_runs`) | System | Issue → `failed` unconditionally — this path does *not* exempt `prd`-kind runs the way a normal failed submit does |
| `queued` | `cancelled` | `POST /runs/{id}/cancel` with a reason (US-27.10) | Manager (capability-gated) | Every work item the run covered returns to its pre-dispatch status; `run-cancelled` events recorded |
| `running` | `cancelled` | The same call, which requests the cooperative stop; the run lands when the worker calls `acknowledge_stop` | Manager → worker | As above. Work is never killed mid-flight |

The `succeeded` and `failed` rows above reach the same `perform_submit` logic through two
different transports: success goes through one of the five `submit_*` MCP tools
(`factory_mcp.py`), while a failure is reported by the runner calling the plain-HTTP
`POST /worker/runs/{run_id}/submit` route (`routers/worker.py`) directly with `error` set — no MCP tool
involved.

A run never moves backward from `succeeded` or `failed` — a retry is a brand new run row,
produced by re-dispatching the issue, not a status change on the old one.

### Gate result

`approvals.gate` (`031_issues.sql:205-207`) names six gates: `prd`, `plan`, `code-review`,
`qa-signoff`, `merge-override`, `promotion`. `approvals.decision` allows `approved`, `rejected`,
`sent-back` — but no single gate uses all three; each gate only ever writes the decisions its own
router calls with:

| Gate | Decisions actually written | Trigger | Who can |
|---|---|---|---|
| `prd` | `approved`, `sent-back` | `POST /issues/{id}/prd/approve`, `POST /issues/{id}/prd/send-back` | Manager (web) |
| `plan` | `approved`, `sent-back` | `POST /issues/{id}/plan/approve`, `POST /issues/{id}/plan/send-back` | Manager (web) |
| `code-review` | `approved`, `rejected` (never `sent-back`) | `POST /runs/{id}/approve`, `POST /runs/{id}/reject` | Manager (web) |
| `merge-override` | `approved` only — no reject path found | `POST /issues/{id}/merge-override` | Manager (web) |

`approvals` is append-only — a "result" is a new row, never an update to a prior one.
`merge-override` doesn't merge anything by itself: it only records a comment-backed approval for
context — the manager still has to call the `code-review` gate's own approve to actually merge.
US-21.7 retired the `qa-signoff` and `promotion` endpoints. Their gate values stay in the check
constraint so historical rows remain readable, but nothing writes them now — a release's own
sign-off and promotion are recorded on the `releases` row, and unlike the old gates they DO drive
status: sign-off is what unlocks promotion, and promotion is what ships the pinned build.

### Review decision (code-review gate)

This is the one gate result with real teeth, so it earns its own callout: `code-review` is the
only gate wired to `approve_run`/`reject_run`, the RPCs that actually move `issues.status` (see
the Issue status table above — `in-review` → `merged` on `approved`, `in-review` → `needs-fixes`
on `rejected`). Every other gate's decision is a log entry with no status side effect of its own.

### Deployment state

`deployment_runs.status` (`infra/supabase/migrations/021_deployment_runs.sql:20-21`, widened by
`026_current_run_and_cancel.sql:19-22`) allows: `queued`, `running`, `succeeded`, `failed`,
`cancelled`. A `deployment` (the target definition) has no status column of its own —
`deployment_runs` is where "state" actually lives, one row per execution.

Two pipelines produce those rows, picked by `deployments.kind` inside `deploy.launch` and
nowhere else. A **factory** run resolves the branch head, transfers the payload over SFTP,
extracts it and runs the deployment script. An **external** run (US-50.2) resolves the source
commit, opens or reuses a pull request into `target_branch`, and merges it **with a merge
commit** — never squash, never rebase. It ends there: success means the merge commit exists on
the target branch, not that anything was deployed. A target branch that already contains the
source commit succeeds as a stated no-op; a merge GitHub refuses fails the run with GitHub's own
message and **leaves the pull request open**, and the next run finds it rather than opening a
second. `commit_sha` keeps its meaning — the *source* commit — because
`GET /issues/{id}/deployments` tests it for ancestry; the merge commit and PR number live in
`deployment_runs.merge_commit_sha` / `pr_number`, null for every factory run.

| From | To | Trigger | Who can | Side effect |
|---|---|---|---|---|
| (none) | `queued` | Manager triggers a run — `POST /deployments/{id}/run`, `/zip`, `/redeploy-zip`, `/runs/{rid}/redeploy`, `/runs/{rid}/promote`, or `/rollback` | Manager (web); owners-only when the deployment is `protected` | A `deployment_runs` row inserted; single-flight — a deployment can't have two `queued`/`running` rows at once |
| `queued` | `running` | The launched pipeline picks the run up | System (`deploy.launch`) | `started_at` set |
| `running` | `succeeded` | The pipeline completes without error | System | `finished_at` set; `deployments.current_run_id` updated |
| `running` | `failed` | The pipeline raises or hits the deployment's `run_timeout_minutes` (default 30); the API process restarts mid-run (`reap_orphaned_runs`, startup); or — US-120.1 — `settle_stranded_release_deploys` finds a release-linked run past that timeout plus five minutes with no live task in the process | System | `finished_at` set; log annotated |
| `queued` / `running` | `cancelled` | Manager cancels (`POST /deployments/{id}/runs/{rid}/cancel`), or — US-120.1 — stops/rejects the release the run belongs to (`POST /releases/{id}/cancel` / `/reject` at `deploying`) | Manager (web); owners-only when `protected` | If a live pipeline task exists it's cancelled cooperatively; otherwise the row is flipped directly |

`promote_run` and `rollback_deployment` both refuse to act on a source run that isn't already
`succeeded` — promotion and rollback replay a known-good run's payload onto another deployment,
they don't reach into an in-flight one.

**A release-linked run settles its release (US-63.2, made total by US-120.1).** A run with
`deployment_runs.release_id` set is the release's UAT deploy leg, and *every* writer that ends
it moves `releases.status` off `deploying` — `succeeded` → `uat-deployed`, anything else →
`uat-deploy-failed` with the writer's reason on `releases.failure_reason`: the pipeline's four
terminal branches, the startup reaper, and `request_cancel`'s no-live-task branch. The update is
guarded to `status = 'deploying'`, so whoever moved the release first wins and a late writer is
a no-op. `deploy.settle_stranded_release_deploys` (from `sweeps.lease_sweep_tick` — at startup and every 30 s)
re-reads any release still at `deploying` from its run — terminal or missing run → settled to
match; a `queued`/`running` run older than the deployment's timeout + 5 min that this process
does not hold is failed and settled; a young or live one is left alone. Before US-120.1 the
reaper flipped only the run, and a release whose deploy died with the process (2026.08.18.2,
2026-08-18) stayed `deploying` with no legal exit while migrations 215/275 froze its project.
The production leg (`promoting`, `prod_deployment_run_id`) is **not** linked this way — see
US-120.2.

### Epic status

`epics.status` (`031_issues.sql:33`) allows `open`, `completed` — the only two-way toggle in
this document. Reached entirely through direct Supabase writes from the web app
(`epic-actions.tsx`), not through `api`:

| From | To | Trigger | Who can | Side effect |
|---|---|---|---|---|
| `open` | `completed` | Manager clicks "Complete epic" (direct `epics` update via the Supabase JS SDK) | Manager (web) | Blocked server-side by a trigger (`guard_epic_completion`) if any non-abandoned child issue is still in an open status (anything but `merged`/`done`) |
| `completed` | `open` | Manager clicks "Reopen epic" (same direct update) | Manager (web) | No guard — reopening is unconditional |

### Test case status

`test_cases.status` (`008_test_cases.sql:16`) allows `active`, `abandoned`:

| From | To | Trigger | Who can | Side effect |
|---|---|---|---|---|
| `active` | `abandoned` | An issue's plan is re-approved (`POST /issues/{id}/plan/approve`) while the issue already has agent-sourced active test cases from a prior plan | System, inside `approve_plan` | The prior agent-authored cases are superseded by the newly materialized set — re-planning doesn't leave the old test-plan cases active alongside the new ones |
| `abandoned` | `active` | Manager clicks "Restore to active" on a test case in the test library (`setCaseStatus`, a direct `test_cases.status` update via the Supabase JS SDK in `apps/web/src/app/(app)/tests/test-library.tsx`) | Manager (web, direct CRUD) | No guard — restores unconditionally; the same Archive/Restore control also flips `active` → `abandoned` directly in the other direction, for both human- and agent-sourced cases |

Terminal states, by object: an **issue** is done for good at `merged` or `done` — nothing
dispatches, claims, or reviews it further, though the underlying PR can still be reverted
(`POST /issues/{id}/revert`) without moving the issue's status back. `failed` and `needs-fixes`
are recoverable, not terminal — both redispatch. A **run** never leaves `succeeded` or `failed`;
recovery always means a new run row, not a status change on the old one. A **deployment run**
is done at `succeeded`, `failed`, or `cancelled`. An **epic** has no terminal state — `completed`
reopens freely.

When a worker finds a run in a state it didn't expect — a claim it doesn't recognize, a kind it
can't handle, a run whose issue has moved on without it — the correct move is `release_work`, not
forcing a submit to make the state match its assumptions. `release_work` only requires that the
worker still holds the claim; it hands the run back to the pool with an optional note rather than
leaving a claim to expire silently or, worse, a submit landing against a run the caller
misunderstood.

