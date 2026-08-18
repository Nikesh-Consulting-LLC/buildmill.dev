_Part of the [application reference](../../APPLICATION.md) — the index, audience guide, and rules & invariants live there. Keep this file current in the same commit as the change it describes._

## Interface catalog

One subsection per surface from [Actors & surfaces](#actors--surfaces). No request or response
schemas here — an agent that needs a field name goes to the OpenAPI doc (`GET /openapi.json`) or
the MCP tool's own parameter list; these tables answer *what it does* and *what it changes*, which
don't drift the way a hand-copied schema does.

### Web UI

One row per route under `apps/web/src/app/`; a dynamic segment (`[id]`) collapses to one row
covering every project, issue, epic, or run the manager visits — not one row per instance. A route
that only redirects to another row in this table is folded into that row rather than getting one
of its own.

**Dashboard**

| Route | What the manager does here | What it writes |
|---|---|---|
| `/` , `/workbench` | The **Workbench** (renamed from Workdesk/"Things to Do", route from `/dashboard`, 2026-08-14) — what agents hold now, the first five of the queue, release state, and every item waiting on a decision, filterable by project; `/` is a bare redirect here and `/dashboard` 301s to `/workbench` | Nothing directly — links out to the actions that do |
| `/activity` | Org-wide activity feed, a read model over the `activity_feed` view | Nothing — read-only |
| `/help` | The operator handbook — architecture map, lifecycle flowchart, setup guides | Nothing — read-only |

**Projects**

| Route | What the manager does here | What it writes |
|---|---|---|
| `/projects` | Project list — create a project (repo link, branches, slug). **US-118.3:** the New project dialog chooses the template from a single scroll-snapping row of cards (cover, name, three lines of description) — org default preselected, prev/next, category chips at ≥ 2 categories, a filter box past 6, keyboard as a radio group; the line under the row always names the selection | New `projects` row (`org_template_id`) |
| `/projects/[id]` | One project's home — links to epics, releases, deployments, connect, audit, issues | Nothing directly — a hub |
| `/projects/[id]?tab=instructions` | The project's instruction files in the templates' shape (US-114.2): Task processing + the grouped tree of `AGENTS.md` and `.buildmill/*.md` on the left, one editor on the right; the template banner (US-114.3) — which org template, how many files differ, Reset to template, Export/Import zip, Change template (US-118.4: each option is the template's row card — cover, name, key, one line of description, file count); mark-ready, publish, History, refresh above. The Overview's *Created from template* line carries the template's thumb. `?tab=guidelines` / `?tab=worker-instructions` still land here | `projects.agent_instructions`, `worker_instructions` rows, `projects.org_template_id` (Change template) — direct Supabase; publish via `POST /projects/{id}/guidelines/save-instructions` |
| `/projects/[id]/epics` | Epic list with rollup progress per epic | Nothing — read-only |
| `/projects/[id]/epics/[epicId]` | One epic's issues grouped by type; complete/reopen the epic | `epics.status` via a direct Supabase update, guarded server-side by `guard_epic_completion` (see [Epic status](#epic-status)) |
| `/projects/[id]/connect` | Everything a worker needs to attach: MCP URL, git remote, and this project's Power Git grants | `git_power_grants` rows (grant/revoke) via direct Supabase |
| `/projects/[id]/audit` | Guideline/instruction edit history and the content-audit trail | Nothing — read-only |
| `/projects/[id]/deployments/[deploymentId]` | One deployment's config, run history, env vars, drift, health check | Deployment runs/env vars — see the `deployments.py` rows below |
| `/releases` | US-21.6: every release across visible projects, with the current UAT and Production build marked | Nothing — a hub |

**Issues**

| Route | What the manager does here | What it writes |
|---|---|---|
| `/issues` | Cross-project issue hub — multi-select projects, Outline/Board/Table/Timeline lenses, define issues inline | New `issues` rows (direct Supabase insert) |
| `/issues/[id]` | One issue's detail — story body, acceptance criteria, plan/PRD, comments, dispatch, revert. US-48.2 adds a **Wireframe** tab (before Plan) on every non-feature type: the drawn screen in a sandboxed iframe, with Redo | `POST /issues/{id}/dispatch`, `/revert`; `issue_comments`; direct field edits |

**Review**

| Route | What the manager does here | What it writes |
|---|---|---|
| `/review` | Retired standalone queue — redirects to `/workbench` | Nothing |
| `/review/[issueId]` | The review panel: diff and story side by side, gate results inline — approve/reject a run, approve/send-back a plan or PRD, log a merge-override | `POST /runs/{id}/approve`, `/reject`; `/issues/{id}/plan/approve`, `/plan/send-back`, `/prd/approve`, `/prd/send-back`, `/merge-override` |

**Tests**

| Route | What the manager does here | What it writes |
|---|---|---|
| `/tests` | Test case library per project — create/archive/restore cases, launch a UAT run | `test_cases` (direct Supabase CRUD + status toggle, see [Test case status](#test-case-status)) |
| `/tests/runs/[id]` | One test run's checklist — record pass/fail per case | `test_run_results` (direct Supabase) |

**Team & servers**

| Route | What the manager does here | What it writes |
|---|---|---|
| `/team` | Roster (members + role), Live (connected workers/agents), Connect (MCP/git snippets) tabs — provision members, issue router tokens, edit Power Git grants | `organization_members`, `principals`; `POST /orgs/{id}/members/provision`, `/reset-password` |
| `/team/[principalId]/runner` | One agent's runner console — live presence, health, current work item, command feed, server-side config editor | `PATCH /runner/{worker_id}/config`; `POST /runner/{worker_id}/command` |
| `/workers` | Redirects to `/team?tab=live` (folded in, US-9.15) | Nothing |
| `/workers/[id]` | Redirects to `/team?principal=<id>` — the agent's profile is its one home (US-35.1) | Nothing |
| `/servers` | **Machines.** Every machine the factory can reach over SSH, in one list — deploy targets, agent hosts, or both. Each card carries its agent fleet summary (status, agent count, drift, probe freshness) when the machine has been provisioned (US-35.2) | `POST /servers`; `PATCH/DELETE /servers/{id}`, `.../test`, `.../trust-host-key` |
| `/servers/[id]` | One machine: Access (credential, host key, what deploys here) plus SSH/Files/Test/Edit/Delete; and when provisioned, the full agent surface — Overview (health, installed vs current agent code), Agents (enable/pause/restart/remove, add; each says **why** it is idle and offers "Re-issue token" when revoked — US-27.9), Setup (modules, packages, new-agent template, teardown), Activity (job history + live log). An unprovisioned machine is offered provisioning with itself preselected (US-35.2) | `POST /agent-servers/{id}/provision · /update · /probe · /teardown · /slots · /slots/{slot_id}/reissue-token`; `GET .../slots/idle-reasons`; `PATCH /agent-servers/{id}` and `.../slots/{slot_id}`; `DELETE .../slots/{slot_id}` |
| `/agent-servers` | Redirects to `/servers` — agent servers were never a second kind of object (US-35.2) | Nothing |
| `/agent-servers/[id]` | Redirects to `/servers/<machine>`, resolving the host id so old links land (US-35.2) | Nothing |
| `/files/[id]`, `/terminal/[id]` | Chrome-less "pop out" windows for one server's file manager / SSH terminal (same panels embedded in the server card) | File manager: `POST/GET /servers/{id}/files/*`; terminal: whatever the manager types, over the `WS /servers/{id}/terminal` bridge |

**Settings**

| Route | What the manager does here | What it writes |
|---|---|---|
| `/settings` | Redirects to `/settings/llm-providers` | Nothing |
| `/settings/github` | Connect GitHub (App install or PAT), disconnect | `github_connections`; `POST /github/connections/pat`, `.../disconnect` |
| `/settings/llm-providers` | Configure thinking-task LLM providers and which function routes to which | `llm_providers`, `llm_function_routes` (direct Supabase; keys via a write-only vault RPC) |
| `/settings/notifications` | Webhook notification endpoints — add/delete/test | `POST/DELETE /notifications/endpoints`, `.../test` |
| `/settings/team`, `/settings/tokens`, `/settings/workers` | Redirects to `/team` (folded in, US-9.13/9.14) | Nothing |
| `/settings/project-templates` | The org's project templates — copy from the catalog (US-118.2: each catalog row shows its cover and a line of description), edit each of the seventeen files, set default, hide/archive; **Export** a template as a zip of its files and **Import** a zip over the selected template (US-114.1, Owner/Admin). **Edit details** (US-118.2): name, category, markdown description and cover — upload to `<org>/<template>/cover`, pick a built-in, or remove — with a live card preview; **New custom template** opens the same form (Owner/Admin) | `org_project_templates`, `org_project_template_sections` — direct Supabase under `manage_project` RLS; `copy_project_template_into_org` RPC; `template-images` bucket under Storage RLS |

**Admin** (platform-admin only)

| Route | What the manager does here | What it writes |
|---|---|---|
| `/admin` | Redirects to `/admin/orgs` | Nothing |
| `/admin/orgs` | Every organization — create, rename, archive, delete | `POST/PATCH/DELETE /admin/orgs`, `.../archive` |
| `/admin/users` | Every user — edit, deactivate, reset password, edit org memberships | `PATCH /admin/users/{id}`, `.../deactivate`, `.../reset-password`; `POST/PATCH/DELETE /admin/memberships` |
| `/admin/roles` | The six-role capability matrix | `PUT /admin/role-capabilities`, `POST .../reset` |
| `/admin/prompt-templates`, `/admin/prompt-templates/[...key]` | List every template; edit one thinking/worker-instruction/guideline-section override | `PUT/DELETE /admin/prompt-templates/{key}` |
| `/admin/project-templates` | The superadmin catalog of project templates — create, duplicate, set default, disable, delete; edit each file; **Export**/**Import** a template as a zip (US-114.1). **Edit details** (US-118.1, replaces the inline rename): name, key, category, markdown description and cover — upload to `catalog/<id>/cover` from the browser under Storage RLS, pick a built-in, or remove — with a live card preview; the header shows the thumb and the description as markdown | `GET/POST/PATCH/DELETE /admin/project-templates` (PATCH accepts `image_path` as null / `builtin/<name>` / `catalog/<id>/cover`, else 422; DELETE removes the cover object best-effort), `.../duplicate` (carries a built-in cover, not an upload), `PUT/DELETE .../sections/worker_instruction/{kind}`; `template-images` bucket |

**Auth & standalone**

| Route | What the manager does here | What it writes |
|---|---|---|
| `/login` | Sign in | A Supabase Auth session |
| `/change-password` | Set a new password — forced when `must_change_password` is set (admin-provisioned or after an admin reset), open to anyone otherwise | The Auth user's password; `principals.must_change_password` cleared |
| `/profile` | Own profile fields, personal router tokens | `profiles`; personal access tokens |

### Supabase (direct)

Not a router file — this is the browser calling the Supabase JS SDK straight from `apps/web`,
authorized by the caller's own session and scoped by RLS, with no `api` process in the path. It's
where the web app's plain CRUD actually lives; a fair number of rows in the Web UI table above
already point here rather than at an endpoint. Grouped by what the tables are for, not listed
call by call:

| Tables | What the web app writes there | When |
|---|---|---|
| `issues`, `epics`, `issue_comments`, `issue_events`, `clarifications` | Fields the dispatch/approval endpoints don't own — title, description, epic/assignee/reviewer links, target date, abandon/restore — plus comments, event-log entries, and clarification answers | Editing an issue or epic outside a status transition; posting a comment; answering a clarification |
| `test_cases`, `test_runs`, `test_run_results` | The test case library and UAT sessions | Creating/archiving/restoring a test case; launching a run; recording pass/fail per case |
| `projects`, `deployments`, `project_guidelines`, `worker_instructions`, `project_learnings` | Project-level config and knowledge-base content, plus a deployment's own settings (not its runs, which are FastAPI's) | Editing a project's summary/environment/release-branch fields, a guideline section, worker instructions, the learnings doc, or a deployment's config |
| `organization_members`, `principals`, `workers`, `git_power_grants`, `profiles` | Roster, identity, and access grants | Changing a member's role; switching active org; clearing a forced-password flag; revoking a worker/token; granting or revoking Power Git; editing a profile |
| `llm_providers`, `llm_function_routes` | Thinking-task LLM provider config and routing | Adding, editing, or deleting a provider; changing which function routes where |
| `documents`, `notifications`, `deployment_notifications` | Attachments and delivery/read state | Uploading a document; marking a notification read; editing a deployment's alert routing |

Anything that needs a secret (server credentials, GitHub tokens, provider API keys), a GitHub
operation, a dispatch (a new `runs` row), or a cross-org effect must go through FastAPI
orchestration instead — RLS scopes a session to its own org, but it can't hold a secret or reach
an external system, so those stay server-side by construction, not by convention. RLS, not
application code, is what keeps this surface org-scoped: every write above is subject to the same
`is_org_member(org)` policies as any other row in the schema, not a check the web app remembers to
run.

### FastAPI orchestration

All routes below are mounted under `/api/v1` (`apps/api/app/main.py`) except `gitproxy.py`,
mounted at the repository root — its paths are exactly `/git/...`, matching the remote URL
`get_work_context` hands a worker — and the Worker MCP app, a third mount root at `/mcp` (see
[Worker MCP](#worker-mcp) below). `GET /api/v1/health` is a bare liveness probe (no auth, no
state) and isn't given its own row. Two rollups below (marked *rollup*): pairs of endpoints that
differ only in verb over the same write-only value.

**`auth.py`** — tells the web app who it is and which org it's currently acting in.

| Endpoint | Caller | What it does | What it changes |
|---|---|---|---|
| `GET /auth/me` | Web app (any authenticated session) | Resolve the caller's user id, email, and (first) org id from their JWT | Nothing — read-only |

**`worker.py`** — the plain-HTTP worker pool contract; the runner process calls `/submit` directly
on failure, and every MCP tool in `factory_mcp.py` that mutates a run calls through the shared
`perform_submit`/`perform_add_comment` functions defined here rather than duplicating the logic.

| Endpoint | Caller | What it does | What it changes |
|---|---|---|---|
| `GET /worker/pool` | Worker (HTTP) | List claimable plan/code runs; self-heals by reconciling expired-with-pushes claims first | Nothing directly (the reconciler may auto-submit stale claims) |
| `POST /worker/runs/{run_id}/claim` | Worker (HTTP) | Atomically claim a run, gated by the worker's project/kind capabilities | `runs.worker_id`, `claim_expires_at`; issue status advances for `plan`/`code` kinds |
| `GET /worker/runs/{run_id}/context` | Worker (HTTP) | One-call context bundle: story, branch, git remote, instructions, comment thread | Extends the claim's lease as a side effect |
| `POST /worker/runs/{run_id}/heartbeat` | Worker (HTTP) | Extend a held claim's lease | Claim lease |
| `POST /worker/runs/{run_id}/comment` | Worker (HTTP) | Post to the work item's comment thread; extends the lease | `issue_comments`; lease extended |
| `POST /worker/runs/{run_id}/submit` | Runner (HTTP) / worker (HTTP) | The submit contract — success (plan/PRD/stories/code, no `error`) or failure (`error` set); see [Run outcome](#run-outcome) | `runs.status`, `issues.status`; artifacts, `test_cases`, or a GitHub PR depending on run kind |
| `POST /worker/runs/{run_id}/release` | Worker (HTTP) | Hand a claim back to the pool with an optional note | `runs.worker_id` cleared; `issues.status` forced to `queued` |
| `POST /worker/runs/{run_id}/documents` | Worker (HTTP) | Agent mid-run upload attached to the work item | New `documents` row (`source=agent`) |
| `GET /worker/runs/{run_id}/documents/{document_id}` | Worker (HTTP) | Byte-fetch a work-item or governing-PRD document | Nothing — read-only |

**`issues.py`**

| Endpoint | Caller | What it does | What it changes |
|---|---|---|---|
| `POST /issues/{issue_id}/complexity-score` | Manager (web) | Score or refine an item's advisory complexity from its plan (if any) or story | `issues` complexity fields |
| `POST /issues/{issue_id}/dispatch` | Manager (web) | Dispatch via the `dispatch_issue` Postgres function; see [Issue status](#issue-status). US-22.10: refuses a **code** dispatch for a story whose project is in `feature`/`epic` mode and whose feature owns the build, unless the story is in trouble | New `runs` row; `issues.status` → `queued`. **Side effect (US-22.4/22.7):** best-effort write of `AGENTS.md`/`CLAUDE.md` (only when the block's hash changed) and of the docs tree, so the agent reads current instructions and the story it just published. Never blocks the dispatch |
| `POST /issues/{issue_id}/batch-dispatch` | Manager (web) | US-20.5: dispatch **every** story in this feature in `sub_no` order (`dispatch_feature_batch`), phase inferred from the children. `feature`/`epic` build mode only; a non-dispatchable child is reported in `skipped`, not fatal | One `runs` row per dispatched child; each child's `issues.status` → `queued` |
| `POST /issues/{issue_id}/revert` | Manager (web) | Revert a merged issue's PR on GitHub | GitHub revert PR opened; `issue_events` (`reverted`) |
| `GET /issues/{issue_id}/deployments` | Manager (web) | Whether this issue's merge commit is live on each of the project's deployments | Nothing — read-only (best-effort backfills `runs.merge_commit_sha`) |

**`workflow.py`** — PRD drafting, story breakdown, the plan gate, and release sign-off.

| Endpoint | Caller | What it does | What it changes |
|---|---|---|---|
| `POST /issues/{issue_id}/prd/draft` | Manager (web) | Dispatch a `prd`-kind run into the worker pool | New `runs` row |
| `POST /issues/{issue_id}/prd/approve` | Manager (web) | Approve the draft PRD; records the chosen breakdown mode | `artifacts.status` → `approved`; `approvals` row; `issues.status` → `ready` |
| `POST /issues/{issue_id}/prd/send-back` | Manager (web) | Send the PRD back with a required comment; dispatches a fresh `prd` run carrying it as feedback | `approvals` row (`sent-back`); prior `artifacts` → `superseded`; new `runs` row |
| `PATCH /artifacts/{artifact_id}` | Manager (web) | Edit a still-draft artifact's content | `artifacts.content` |
| `POST /issues/{issue_id}/breakdown/dispatch` | Manager (web) | Dispatch a `breakdown`-kind run for a ready feature with an approved PRD | New `runs` row |
| `POST /issues/{issue_id}/test-run/dispatch` | Manager (web) | US-13.11: send the submitted code run's branch for staffed verification (`test`-kind run); refuses without a submission or while one is in flight | New `runs` row (kind `test`); issue status untouched |
| `POST /issues/{issue_id}/wireframe/dispatch` | Manager (web) | US-48.2: put an agent on drawing ONE story, before it is planned. The same endpoint is the redo — `feedback` is the manager's comment, and it reaches the agent alongside the wireframe it replaces. `issues.status` is never touched | New `runs` row (kind `wireframe`) |
| `POST /issues/{issue_id}/wireframes/batch-dispatch` | Manager (web) | US-48.3: one wireframe run per child story of this feature, in `sub_no` order. Abandoned, in-flight and already-drawn children are reported in `skipped`, not fatal; every child skipped is a no-op, not an error. Unlike the code batch this is **not** gated on build mode | One `runs` row per dispatched child |
| `GET /issues/{issue_id}/wireframe/preview` | Manager (web) | US-48.2: the wireframe as one self-contained HTML page (kit inlined), for the app's sandboxed iframe | Nothing — read-only |
| `POST /issues/{issue_id}/plan/approve` | Manager (web) | Approve the draft plan/test plan; materializes its test cases | `artifacts.status` → `approved`; `test_cases` created; `issues.status` → `planned` |
| `POST /issues/{issue_id}/plans/approve-all` | Manager (web) | US-20.6: clear the plan gate for every child story of this feature that is in `plan-review` — approves only, never sends back; a child in another status is skipped, not failed | Per approved child: artifacts → `approved`, `approvals` rows (`plan`), materialized test cases, `plan-approved` event, `issues.status` → `planned` |
| `POST /issues/{issue_id}/plan/send-back` | Manager (web) | Send the plan back with a required comment; issue returns to its pre-dispatch status | `approvals` row (`sent-back`); `issues.status` |
| `POST /issues/{issue_id}/replan` | Manager (web) | Supersede the approved plan and dispatch a fresh one | `artifacts.status` → `superseded`; new `runs` row |
| `POST /issues/{issue_id}/merge-override` | Manager (web) | Record a soft, comment-backed approval — a note for context, not a merge | `approvals` row (`merge-override`); see [Gate result](#gate-result) |

**`reviews.py`** — the code-review gate with real teeth (see [Review decision](#review-decision-code-review-gate)).

| Endpoint | Caller | What it does | What it changes |
|---|---|---|---|
| `POST /runs/{run_id}/approve` | Manager (web) | Merge the run's PR on GitHub (a no-op for simulated PRs), then call `approve_run` | `issues.status` → `merged`; PR merged; `approvals` row; a parent feature may auto-complete |
| `POST /runs/{run_id}/reject` | Manager (web) | Call `reject_run` with a required comment | `issues.status` → `needs-fixes`; `approvals` row |
| `POST /runs/{run_id}/force-requeue` | Manager (web) | US-13.6: one-click recovery for a run whose worker went silent — release the claim back to the pool | Run → `queued`, claim cleared; `claim-expired` event |

**`deployments.py`**

| Endpoint | Caller | What it does | What it changes |
|---|---|---|---|
| `POST /deployments/{id}/run` | Manager (web; owners-only if `protected`) | Trigger a run from the branch, or a one-off `ref` override — the override is refused outright on a `protected` deployment, even for an owner. On an `external` deployment this merges the source into `target_branch` through a pull request and stops there (US-50.2) | New `deployment_runs` row; the pipeline matching the deployment's kind is launched |
| `POST /deployments/{id}/agent-dispatch` | Manager (web) | US-13.13: hand this deployment's execution to an agent (`deploy`-kind pool run), capturing the ref and the one allowed auto-rollback authorization; refuses protected deployments and production without `agent_dispatch_allowed` | New `runs` row (kind `deploy`, issue-less, names the deployment) |
| `POST /deployments/{id}/duplicate` | Manager (web; owners-only if `protected`) | Clone a deployment's config and env values into a new sibling | New `deployments` row; env values copied server-side |
| `POST /deployments/{id}/runs/{run_id}/cancel` | Manager (web; owners-only if `protected`) | Cancel an in-progress run | `deployment_runs.status` → `cancelled` |
| `GET /deployments/{id}/drift` | Manager (web) | How far the branch has moved past the deployed payload, plus what would ship next. On an `external` deployment the comparison is `target_branch...branch` — what the next merge would carry — which needs no run history, so it answers before anything has run (US-50.3) | Nothing — read-only |
| `POST /deployments/{id}/zip` | Manager (web; owners-only if `protected`) | Upload and stage a zip artifact, then run it — **refused with 400 on an `external` deployment** | Staged zip in the data bucket; new `deployment_runs` row |
| `POST /deployments/{id}/redeploy-zip` | Manager (web; owners-only if `protected`) | Rerun the already-staged zip without re-uploading — **refused on `external`** | New `deployment_runs` row |
| `POST /deployments/{id}/runs/{run_id}/redeploy` | Manager (web; owners-only if `protected`) | Rerun an archived payload as-is through the deployment's current pipeline — **refused on `external`** | New `deployment_runs` row |
| `POST /deployments/{id}/runs/{run_id}/promote` | Manager (web; target's own `protected` rule applies) | Ship an exact tested payload to a sibling deployment on the same project — **refused if either side is `external`**, which has nowhere to put a payload | New `deployment_runs` row on the target |
| `GET /deployments/{id}/runs/{run_id}/artifact` | Manager (web) | Stream an archived run payload | Nothing — read-only |
| `POST /deployments/{id}/rollback` | Manager (web; owners-only if `protected`) | Repoint `current` to a retained release folder — no re-transfer. **Refused on `external`**: recovery there means merging a fix, or reverting on GitHub by hand and recording it with `POST /releases/{id}/rolled-back` | New `deployment_runs` row (kind `rollback`) |
| `POST /deployments/{id}/preflight` | Manager (web) | Standalone connectivity/disk-space/tool check — no transfer, no script — **refused on `external`** | Nothing — read-only |
| `POST /deployments/{id}/health-check` | Manager (web) | Run the configured health check on demand — no deploy. **Refused on `external`**: there is no machine to curl from, and probing the public URL from `api` would be a different measurement wearing the same button | Nothing — read-only |
| `PUT /deployments/{id}/env/{name}` · `DELETE /deployments/{id}/env/{name}` *(rollup)* | Manager (web; owners-only if `protected`) | Set or remove one env var; the value is write-only and never echoed back. **Refused on `external`** — the values exist to be written onto a target machine, and offering the field would promise the other system reads them | The value in the data bucket; a `deployment_env_vars` row |
| `DELETE /deployments/{id}` | Manager (web; owners-only if `protected`) | Delete the deployment and its bucket folder (env values, staged zips, artifacts) | `deployments` row deleted; bucket prefix deleted |

**`projects.py`**

| Endpoint | Caller | What it does | What it changes |
|---|---|---|---|
| `PUT /projects/{id}/build-config/{name}` · `DELETE /projects/{id}/build-config/{name}` *(rollup)* | Manager (web) | Set or remove one build-config value; write-only, only the NAME is ever echoed back | The value in the data bucket; a `project_build_config` row |
| `GET /projects/{id}/guidelines.md` | Manager (web) / same function backs a dispatched run's `input_context` | Assembled guidelines markdown (`assemble_project_guidelines`) | Nothing — read-only |
| `POST /projects/{id}/guidelines/save-instructions` | Manager (web) | US-22.6: write the factory-owned **fenced region** of `AGENTS.md` and `CLAUDE.md` now. Goes through the same assemble-and-merge path as the docs sync, so this and a plan approval produce byte-identical files | One GitHub commit carrying both files on the default branch — either both land or neither does |
| `POST /projects/{id}/guidelines/refresh` | Manager (web) | US-43.2 → **us-100.5**: queues a project-scoped `guidelines` run (no work item, `queue_rank = -1`) that studies the repository and proposes revised instruction **files**. Body `{scope: 'all' \| 'document', focus}` — `all` = the Agent Instructions and any per-task file, `document` = AGENTS.md only (`existing` is read as `document`). 409 names an open refresh; 400 for a project with no repository | `runs` row (`kind = guidelines`) + `guideline_refreshes` row (`pending`) |
| `GET /projects/{id}/releases/preview` | Manager (web) | US-21.1: what cutting a release now would produce — proposed version, the commit that would be pinned, the work items merged since the last **released** version, and `blockers` | **Nothing** — read-only |
| `POST /projects/{id}/releases` | Manager (web) | US-21.1: cut a release — pins the default branch head, snapshots included items, git-tags the commit, **creates `release/<version>` at that commit** (US-50.4), and queues the release run. Version is proposed `YYYY.MM.DD.N` and may be overridden. A branch that cannot be created is reported as `branch_error` and is never fatal, exactly like `tag_error` | New `releases` row; a git tag; a `release/<version>` branch |
| `POST /projects/{id}/releases/{release_id}/dispatch` | Manager (web) | US-21.3: queue (or re-queue) the release's agent job. A re-dispatch carries what the release has already reached, and the agent resumes rather than rewriting stored notes | New `runs` row (kind `release`, issue-less); `releases.status` → `queued` |
| `POST /projects/{id}/wireframes/sync` | Manager (web) | US-48.5: rebuild `docs/wireframes/` from stored artifacts — pages, index, README and the kit — as one commit. The retry after a hand-back-time write failed, and how a kit upgrade reaches a project. No enable flag | One commit on the default branch; paths the generation no longer produces are deleted |
| `POST /projects/{id}/docs-tree/sync` | Manager (web) | US-13.4: scaffold or rebuild the repo docs tree from approved state (also the retry after a failed approval-time write); requires `docs_tree_enabled` | One commit on the default branch carrying `docs/factory/` **and** the instruction files. US-22.1: paths under `docs/factory/` that this generation no longer produces are deleted |
| `GET /projects/{id}/learnings.md` | Manager (web) / same function backs a dispatched run's `input_context` | Assembled learnings markdown (`assemble_project_learnings`) | Nothing — read-only |

**`github.py`** — App connect/disconnect and repo/PR/Projects-v2 reads; every read mints a fresh installation token per request, never a cached one.

| Endpoint | Caller | What it does | What it changes |
|---|---|---|---|
| `GET /github/install/callback` | GitHub (browser redirect, no Supabase session — trust comes from the signed state token) | Record a completed App installation | New `github_connections` row (via a service-role RPC) |
| `GET /github/connect-url` | Manager (web) | Mint the GitHub App install URL with a signed state token | Nothing |
| `POST /github/connections/pat` | Manager (web) | Validate a pasted PAT against GitHub identity and every named repo, then store it | New `github_connections` row (PAT stored via a write-only RPC) |
| `POST /github/connections/{id}/disconnect` | Manager (web) | Uninstall the App (if that connection method) and delete the connection | `github_connections` row deleted |
| `GET /github/repos` | Manager (web) | List repos visible to every GitHub connection on the org | Nothing — read-only |
| `GET /github/repos/{owner}/{repo}/pulls` | Manager (web) | List open pull requests | Nothing — read-only |
| `GET /github/repos/{owner}/{repo}/branches` | Manager (web) | List branches | Nothing — read-only |
| `POST /github/repos/{owner}/{repo}/branches` | Manager (web) | Create a branch from the repo's default branch head, or an explicit base | A branch on GitHub |
| `POST /github/projects/{project_id}/issues/pull` | Manager (web) | Retired (US-7.6) — always answers `410` | Nothing |
| `GET /github/repos/{owner}/{repo}/projects` | Manager (web) | List the repo's GitHub Projects v2 boards | Nothing — read-only |

**`llm.py`** — thinking-task LLM calls; Vault-held keys never reach the browser.

| Endpoint | Caller | What it does | What it changes |
|---|---|---|---|
| `GET /llm/functions` | Manager (web) | The backend-owned registry of routable thinking functions | Nothing — read-only |
| `POST /llm/elaborate-test` | Manager (web) | Expand a short test-case description into full steps via the org LLM | Nothing (returns text; the caller decides whether to save it) |
| `POST /llm/learnings/{project_id}/update` | Manager (web) | LLM-merge free-form context into the project's learnings document | `project_learnings.content` |
| `POST /llm/learnings/{project_id}/submissions/{submission_id}/decide` | Manager (web) | Approve (LLM-merges into the document) or reject an agent-submitted learning | `project_learnings.content` on approve; `learning_submissions.status` either way |
| `POST /llm/generate-deploy-script` | Manager (web) | Draft a deployment script from project + guideline context, works for unsaved deployment drafts | Nothing (returns a script) |

**`admin.py`** — platform-admin console; every route requires `require_platform_admin` (a live `is_platform_admin()` check), then writes with the service role, bypassing RLS.

| Endpoint | Caller | What it does | What it changes |
|---|---|---|---|
| `GET /admin/orgs` | Superadmin | List every organization with its owner and member count | Nothing — read-only |
| `POST /admin/orgs` | Superadmin | Create an organization | New `organizations` row |
| `PATCH /admin/orgs/{id}` | Superadmin | Rename or reslug an organization | `organizations` row |
| `POST /admin/orgs/{id}/archive` | Superadmin | Archive or unarchive an org (refused on the seeded platform-admin org) | `organizations.archived_at` |
| `DELETE /admin/orgs/{id}` | Superadmin | Delete an org with no work in progress (refused on the platform-admin org) | `organizations` row deleted |
| `GET /admin/users` | Superadmin | List every user with their org memberships | Nothing — read-only |
| `PATCH /admin/users/{id}` | Superadmin | Edit display name / email; rolls the auth email back if the profile write fails after | `profiles` row; the Auth user's email |
| `POST /admin/users/{id}/deactivate` | Superadmin | Ban or unban a user in Supabase Auth | Auth user ban state |
| `POST /admin/users/{id}/reset-password` | Superadmin | Set a user's password directly | Auth user password |
| `POST /admin/memberships` | Superadmin | Link a user to an org with a role | New `organization_members` row; resolves or creates the `principals` row |
| `PATCH /admin/memberships` | Superadmin | Change a membership's role | `organization_members.role` |
| `DELETE /admin/memberships` | Superadmin | Remove a membership | `organization_members` row deleted |
| `PUT /admin/role-capabilities` | Superadmin | Overwrite the six-role capability matrix; `owner`/`manage_org` and every role's `view` are re-enforced server-side regardless of what's posted | `role_capabilities` rows |
| `POST /admin/role-capabilities/reset` | Superadmin | Restore the shipped default matrix | `role_capabilities` rows |
| `GET /admin/prompt-templates` | Superadmin | Every thinking/worker-instruction/guideline-section/help template, plus any override | Nothing — read-only |
| `PUT /admin/prompt-templates/{key}` | Superadmin | Set an override for one template key | A prompt-override row |
| `DELETE /admin/prompt-templates/{key}` | Superadmin | Clear an override, reverting to the factory default | Prompt-override row deleted |

**`members.py`** — org member provisioning (capability-gated on `manage_members`, then service-role execution).

| Endpoint | Caller | What it does | What it changes |
|---|---|---|---|
| `POST /orgs/{org_id}/members/provision` | Manager with `manage_members` | Create a confirmed human user + principal + membership directly; the one-time password is returned exactly once (no email is sent) | New Auth user; `principals` row; `organization_members` row |
| `POST /orgs/{org_id}/members/{user_id}/reset-password` | Manager with `manage_members` | Set a member's password and flag must-change | Auth user password; `principals.must_change_password` |

**`notifications.py`** — webhook URLs are secrets; they flow browser → `api` → data bucket and are never returned.

| Endpoint | Caller | What it does | What it changes |
|---|---|---|---|
| `POST /notifications/endpoints` | Manager (web) | Register a webhook endpoint | New `notification_endpoints` row; the URL stored in the data bucket |
| `DELETE /notifications/endpoints/{id}` | Manager (web) | Remove an endpoint | `notification_endpoints` row and its stored URL deleted |
| `POST /notifications/endpoints/{id}/test` | Manager (web) | Send a test delivery | Nothing stored — a delivery attempt only |

**`servers.py`** — server registry, SFTP file manager, and the SSH terminal bridge; `api` is the only component that can read the stored credentials.

| Endpoint | Caller | What it does | What it changes |
|---|---|---|---|
| `POST /servers` | Manager (web) | Register a server; credentials go straight to the private data bucket | New `servers` row; a credential object |
| `PATCH /servers/{id}` | Manager (web) | Edit a server, optionally replacing credentials or switching auth method | `servers` row; credential object(s) |
| `DELETE /servers/{id}` | Manager (web) | Delete a server (refused while a deployment still points to it) | `servers` row and its credential prefix deleted |
| `POST /servers/{id}/test` | Manager (web) | Open an SSH connection and report the host-key fingerprint | Nothing (may trust-on-first-use record the host key) |
| `POST /servers/test-connection` | Manager (web) | US-20.4: try credentials from the form **before** the server exists — optional `server_id` still enforces that row's trusted host key | **Nothing** — no row, no Storage object, no host-key capture |
| `POST /servers/{id}/trust-host-key` | Manager (web) | Clear the trusted host key so the next connect re-captures it | `servers.host_key_fingerprint` cleared |
| `GET /servers/{id}/files` | Manager (web) | List a directory over SFTP | Nothing — read-only |
| `POST /servers/{id}/files/upload` | Manager (web) | Upload a file over SFTP | A file on the remote server |
| `GET /servers/{id}/files/download` | Manager (web) | Stream a file over SFTP | Nothing — read-only |
| `POST /servers/{id}/files/mkdir` | Manager (web) | Create a directory | A directory on the remote server |
| `POST /servers/{id}/files/delete` | Manager (web) | Delete a file or directory, optionally recursive | A file/directory on the remote server removed |
| `POST /servers/{id}/files/extract` | Manager (web) | Extract a zip archive in place | Files on the remote server |
| `GET /servers/{id}/files/read` | Manager (web) | Read a text file's content (binary files are refused) | Nothing — read-only |
| `POST /servers/{id}/files/write` | Manager (web) | Write a text file, with optimistic-concurrency conflict detection | A file on the remote server |
| `POST /servers/{id}/files/new` | Manager (web) | Create an empty file | A file on the remote server |
| `WS /servers/{id}/terminal` | Manager (web) | Interactive SSH shell bridged over WebSocket — PTY resize, 15-minute idle timeout | Whatever the manager types, on the remote server |

**`agent_servers.py`** — the agent fleet (Phase 26). Action-only: reading hosts, slots and job
logs goes straight to Supabase under RLS. Every write is gated on the **`manage_org`** capability —
this registers machines that will be handed root-level install commands. The work itself runs as a
background job (`agent_provision.py`) over the same SSH bridge `servers.py` uses, and the only
secret that reaches an agent machine is one worker token per slot, in a 0600 env file.

| Endpoint | Caller | What it does | What it changes |
|---|---|---|---|
| `POST /agent-servers` | Admin (`manage_org`) | Register a machine as an agent server — **runs preflight first** (Debian-family, systemd, sudo, arch, disk, and since US-27.13 `factory-reachable`: the machine curls the API's health endpoint over the SSH session) and refuses with the named check rather than half-installing. A loopback or unset `API_BASE_URL` is refused before any connection is opened | New `agent_servers` row (status `new`); nothing on the machine |
| `PATCH /agent-servers/{id}` | Admin | Edit the host's definition — workdir, modules, extra packages, setup commands, agent sudo, new-agent template. Validated against the runner-config validator plus "the host installs this module" | `agent_servers` row |
| `POST /agent-servers/{id}/preflight` | Admin | Re-run the registration checks | Nothing — read-only |
| `POST /agent-servers/{id}/provision` | Admin | Install (or resume installing) the machine, optionally with N agents. Idempotent | `agent_server_jobs` row; packages, the supervisor bundle, a systemd template unit, and slots on the machine |
| `POST /agent-servers/{id}/slots` | Admin | Add agents. Capacity is **advisory** — over the CPU count or under 10 GB free warns with the numbers and can be confirmed through. `adopt_worker_id` binds an existing agent instead of minting one, re-issuing its token | `agent_slots`, `principals`, `workers`, `runner_config`, `worker_capabilities`; env files and services on the machine |
| `PATCH /agent-servers/{id}/slots/{slot_id}` | Admin | Enable or pause one agent — writes `runner_config.paused` and pushes `config.update`. A claimed run is left to finish. us-116.5: the surfaces now call `POST /agents/{principal_id}/start` / `stop` instead (Start also restarts a dead service); this stays as the low-level flag | `runner_config.paused`, `agent_slots.desired_state` |
| `POST /agent-servers/{id}/slots/{slot_id}/restart` | Admin | Drain, restart the service, restore the previous state | `agent_server_jobs` row; the service on the machine |
| `POST /agent-servers/{id}/slots/{slot_id}/reissue-token` | Admin | US-27.9: mint a new worker token, write it to the slot's 0600 env file, restart, confirm it reconnected — in that order. The repair for a revoked managed agent; un-revoking is never offered | `workers.token_hash`; `agent_server_jobs` row (kind `reissue_token`); the env file and service on the machine |
| `GET /agent-servers/{id}/slots/idle-reasons` | Org member | US-27.9 → us-116.4: each agent's status on this host, keyed by slot — `db.agent_status` (`state` with presence in front, plus the idle-reason word and detail); the same function `/agents/idle-reasons` serves | Nothing — read-only |
| `GET /agents/idle-reasons?org={org_id}` | Org member | US-35.1 → us-116.4: every agent's **status** in the org (`state`: `offline · revoked · working · stopped · no-roles · no-model · no-grants · queue-held · ready`, plus the idle-reason word and detail), keyed by worker **and** principal id — `db.agent_status`, presence (the `live_runner_sessions` view) in front of `worker_idle_reason`. The same function the host-scoped route serves. Org scoping is RLS via the caller's own token | Nothing — read-only |
| `GET /agents/model-check?org={org_id}&kinds=a,b` | Org member | us-116.6: what a NEW agent with these roles would resolve — the same resolver runs and sessions use (`model_resolution.resolve_session`, no pins), so the wizard says "no model will resolve for these roles" once, before the agent exists. Answers `{resolves, model, kind, source, tried}` | Nothing — read-only |
| `POST /agents/{principal_id}/start` | `manage_org` on the **slot's** org | us-116.5: Start means start — `runner_config.paused = false`, `agent_slots.desired_state = enabled`, `config.update` pushed; if the agent is not live (us-116.4's presence) or the last probe found its unit not `active`, the existing restart job is queued for the slot. Refuses a revoked token (409, naming Re-issue), a host with a job running (409), a hand-installed agent (409). Answers `{enabled, restarted: job_id|null}` | `runner_config.paused`, `agent_slots.desired_state`; an `agent_server_jobs` row when restarting |
| `POST /agents/{principal_id}/stop` | `manage_org` on the **slot's** org | us-116.5: today's pause, unchanged in meaning — the service keeps running, the socket stays up, the agent claims nothing, a held run finishes; reads **Stopped**. Never `systemctl stop` | `runner_config.paused`, `agent_slots.desired_state` |
| `PATCH /agents/{principal_id}/name` | `manage_work` | US-32.2: rename an agent across all three name columns (`principals.display_name`, `workers.name`, `agent_slots.name`); infrastructure identity (service name, slot index, workspace, ids) is untouched | those three columns; an `agent_events` row |
| `DELETE /agent-servers/{id}/slots/{slot_id}` | Admin | Uninstall one agent: stop, remove unit + env file, revoke the token, retire the identity. Refused while a run is in flight unless `force=true` | `workers.status`, `organization_members.status`, `agent_slots.status`; the machine |
| `POST /agent-servers/{id}/update` | Admin | Re-push the bundle, re-apply the definition, then restart each agent one at a time, draining first. A slot still busy at the 10-minute ceiling is **skipped and named** — the job lands `partial`, not `succeeded` | `agent_servers.bundle_hash`; services on the machine |
| `POST /agent-servers/{id}/probe` | Admin | Read the machine's health now — since US-31.8 including the kept per-project workspaces' disk footprint | `agent_servers` probe columns (incl. `workspace_bytes`/`workspace_count`), `agent_slots.service_state`; possibly a `runner_incidents` row |
| `POST /agent-servers/{id}/teardown` | Admin | Decommission: every agent removed, units deleted, host soft-removed. Wiping the working folder is opt-in | `agent_slots`, `workers`, `agent_servers.status = 'removed'`; the machine. The `servers` row is untouched |
| `GET /agent-servers/current-version` | Admin | The bundle hash this API would install — the drift comparison point | Nothing — read-only |

**`runner_socket.py`** — the persistent control channel is covered in [Runner WebSocket](#runner-websocket) below; two plain HTTP endpoints sit alongside it for manager-initiated actions:

| Endpoint | Caller | What it does | What it changes |
|---|---|---|---|
| `PATCH /runner/{worker_id}/config` | Manager with `manage_work` | Update a runner's server-side config; pushes it live over the socket if the runner is connected. US-13.8: rejects unknown modules, out-of-range concurrency, invalid autonomy modes, and unparseable regex patterns at write time; the response names what changed. US-31.2/31.5 add `max_run_minutes` (1–1440, or `-1` to clear back to the type default) and `max_item_attempts` (1–20) | `runner_config` row |
| `POST /runner/{worker_id}/policy-preview` | Manager with `manage_work` | US-13.8: evaluate a command line against the runner's stored policy via the same `runner_policy.evaluate` the shell audit uses — allow / hold / block with the deciding pattern | Nothing — read-only |
| `POST /runner/{worker_id}/command` | Manager with `manage_work` | Push a manual command to a connected runner (e.g. a repair); refused if it isn't connected | Nothing stored here — the runner's own `command.audit`/`command.result` frames record the outcome |

**`llm_gateway.py`** — covered in [LLM Gateway](#llm-gateway) below (it's one of the seven surfaces, not just another router):

| Endpoint | Caller | What it does | What it changes |
|---|---|---|---|
| `POST/GET /llm-gateway/{path}` | A coding-agent CLI module's provider SDK | Forward a provider-shaped request to the org's real provider, keyed from Vault; authenticated by a scoped gateway key, not a worker token | Nothing stored — relays to the provider and streams the response back |

**`gitproxy.py`** — covered in [Git proxy](#git-proxy) below; mounted at the repository root, not under `/api/v1`:

| Endpoint | Caller | What it does | What it changes |
|---|---|---|---|
| `GET /git/{org}/{project}/info/refs` | Worker's git client | Smart-HTTP handshake (ref advertisement) for a fetch or a push | Nothing |
| `POST /git/{org}/{project}/git-upload-pack` | Worker's git client | Clone/fetch — capability-gated, streamed straight through to GitHub | Nothing — no factory-side state |
| `POST /git/{org}/{project}/git-receive-pack` | Worker's git client | Push — the ref-update commands are policy-checked before the packfile is forwarded | `runs.pushed_head_sha` (claim path) or `git_power_branch_heads` (Power Git path) on a successful push |

### Worker MCP

A worker connects to the single `POST /mcp` endpoint — the ASGI app `factory_mcp.py` builds,
mounted at `/mcp` in `apps/api/app/main.py`. Every request carries the same `X-Worker-Token`
header as the other worker-credentialed surfaces, **or the same token as
`Authorization: Bearer <token>`** (us-115.1) — MCP's own auth shape, so a client configured the
standard way authenticates. `X-Worker-Token` is checked first and is unchanged; a client that
declares an `Authorization` header also skips its OAuth discovery, which is four `.well-known`
probes this mount can only answer 401 or 404. **There is no project scope on the token**
(us-110.1): a worker reaches every project its `worker_capabilities` grants name — the same list
the pool filter has always used, and the only place an agent's projects are set. The two earlier
answers are both retired: US-3.14's `/mcp/<org>/<project>` URL path (any path segment after `/mcp`
now 404s) and migration 216's `workers.project_id` column, dropped by 279. A tool callable without
a claim defaults `project_id` to the worker's project when it has exactly one grant, and otherwise
requires it explicitly — `list_available_work` and `list_factory_queue` return a `project_id` per
run for that purpose. A worker gets its token from a project's own Connect page
(`/projects/[id]/connect`) or the Connect tab under `/team`.

The most important table here for an operating agent. `factory_mcp.py` defines **53** tools (us-100.5 corrected the long-stale 36; count them with `grep -c '^@mcp.tool(' apps/api/app/factory_mcp.py`). Rows
below are ordered the way a worker actually calls them — discover → claim → gather context → work
→ hand back → follow up, with the always-available standing-context and mid-run tools grouped
where they fit that loop — not alphabetically. Since US-13.5 `get_work_context` returns a compact
brief with an `omitted` manifest; since US-13.3 every `submit_*` carries notes for the manager
that land on the review surface and the item's thread.

| Tool | What it does | What it changes | When to call it |
|---|---|---|---|
| `list_available_work` | Lists claimable plan/code runs in the org's pool, flagging retries | Nothing — read-only | First: see what's available |
| `get_instructions` | Reads a work item's current instruction set — no claim required | Nothing — read-only | Peek before claiming, or re-check mid-run for updated direction |
| `claim_work` | Atomically claims a run; a lost race answers with guidance, not an error | `runs.worker_id`, lease started; issue status advances for `plan`/`code` | After deciding what to work on |
| `get_work_context` | A compact brief (US-13.5): story, acceptance criteria, branch/hand-back mechanics, test-case ids, the plan (code runs) — plus an `omitted` manifest naming how to pull everything else | Extends the claim lease | Immediately after claiming |
| `get_release_changes` | US-21.2: the commit range of the release the claimed prep job is preparing — commits (subject lines), changed file paths with status, the `migrations` and `touched_modules` in the range, and the included work items **with their body, acceptance criteria and the titles of the cases they already carry** (us-101.1). `path_prefix` narrows the file list; `truncated`/`cursor` page it, and a partial range is reported loudly rather than silently capped — including when a prefix was applied to a list GitHub had already capped at 300 | Nothing — read-only | Release prep: before writing anything |
| `get_context_detail` | Pulls one full omitted section: prd, previous_prd, plan, test_plan, previous_plan, feedback, test_cases (full steps), discussion, release_reference | Nothing — read-only | When the brief's `omitted` list points here |
| `get_repo_tree` | Lists the repo's file tree at the run's work branch (or default branch) — no clone needed | Nothing — read-only | Studying the repo, especially before writing a plan |
| `read_repo_file` | Reads one file's text content (size-capped, text only) | Nothing — read-only | Studying specific files |
| `get_workspace` | The working tree, with zero git tooling. **US-31.6: answers `mode: full` (base64 zip) or `mode: delta`** — only `add`/`update`/**`delete`** since what this worker was last served, contents included. Deletions must be applied: a file removed upstream that stays on disk keeps compiling. Any ambiguity (rewritten history, unknown status, size ceiling) answers `full`. **us-98.3: takes an optional `ref`, on merge runs only** — the claim licenses the branches the manager named plus the base, and refuses anything else; a `ref` on any other kind is refused rather than ignored | A `workspace_deliveries` row recording what was served (so the next call can be a delta) — **not** written for an explicit `ref`, because the delta manifest means "the tree you last held" and a second branch would poison it into a silently wrong tree | Code and plan runs; a kept per-project workspace (US-31.8) is refreshed through it; merge runs fetch each branch by `ref` |
| `validate_submission` | Dry-runs a hand-back against the gate's structural checks and reports findings as feedback, never a rejection | Nothing — no GitHub ref is touched | Before submitting, to catch fixable issues early |
| `get_project_guidelines` | Fetches the assembled guidelines markdown (same content committed to `AGENTS.md`) | Nothing — read-only | Any time, not just while holding a claim |
| `get_project_learnings` | Fetches the project's accumulated learnings document | Nothing — read-only | Any time, before repeating a known mistake |
| `list_project_documents` | Lists a project's documents (design docs, PRD material, work-item attachments) with ids | Nothing — read-only | Any time; find an id to read with `get_document` |
| `get_document` | Reads one document's content — markdown/text only | Nothing — read-only | When the frozen context snapshot isn't enough |
| `report_progress` | Heartbeats a long claim: extends the lease and optionally records a progress note the manager sees | Claim lease; optional progress note | During long-running work, so a slow-but-healthy run isn't re-pooled at lease expiry |
| `add_comment` | Posts to the work item's shared comment thread; extends the lease | `issue_comments` row; lease extended | Any point mid-run worth a visible note |
| `request_clarification` | Asks the manager a question mid-run instead of guessing or releasing; claim stays held, lease extends | `clarifications` row | When something is genuinely ambiguous |
| `get_clarifications` | Reads the questions asked on this work item and the manager's answers so far | Nothing — read-only | Poll after `request_clarification`, or on a retry to see the prior exchange |
| `submit_wireframe` | US-48.2: hands back a `wireframe` run — the JSON declaration the wireframe kit renders, or `no_ui_surface` with a reason, which is a **successful** answer for a story that changes nothing a user sees. Shape-tolerant (US-42.1): a JSON string, a bare screen list or one screen as an object are all coerced, never refused | `artifacts` (`wireframe`) at `approved` — there is no gate; `runs.status` → `succeeded`; best-effort commit of `docs/wireframes/<id>.html` | Wireframe run complete |
| `submit_plan` | Hands back a `plan` run: implementation plan + test plan markdown | `runs.status` → `succeeded`; `artifacts` (`plan`, `test_plan`) stored as `draft` | Plan run complete |
| `submit_prd` | Hands back a `prd` run: the four PRD sections | `runs.status` → `succeeded`; `artifacts` (`prd`) stored as `draft`; `issues.status` → `prd-review` | PRD run complete |
| `submit_stories` | Hands back a `breakdown` run: the proposed story split | `runs.status` → `succeeded`; child `issues` created as `draft`; feature `issues.status` stays `ready` | Breakdown run complete |
| `submit_code_work` | Hands back a `code` run as a branch already pushed to the factory git remote — the factory verifies it on GitHub and opens the PR itself | `runs.status` → `succeeded`; `issues.status` → `in-review` (or `merged` on a `direct`-strategy project) | Code run complete, git-native transport |
| `submit_changeset` | Hands back a `code` run as changed files — no git binary, no GitHub account; the factory builds the commit from `base_sha`, pushes, and opens the PR. **US-31.7: refuses any path the repository's own `.gitignore` excludes** (nested files and negation honoured; an already-tracked path is never ignored, so a lockfile update still lands), plus `.factory-out/` and the workspace state file unconditionally | Same as `submit_code_work`, via the same `perform_submit` path, plus the commit itself | Code run complete, git-free transport |
| `report_test_results` | Records pass/failed/blocked against the work item's test cases (their ids come from `get_work_context`); re-reporting a case replaces its prior result; accepts `test`-kind runs too (US-13.11) | `test_run_results` rows | After exercising the manager's test cases against the change |
| `submit_test_run` | Completes a `test` run with a summary — refused with guidance to `release_work` when zero results were reported (US-13.11) | `runs.status` → `succeeded`; issue status untouched | Test run: after reporting per-case results |
| `submit_release_notes` | Completes a claimed release-prep job (US-63.1). Notes whose first line carries the version (mismatch refused), the `notes_doc` declaration the release page renders (shape coerced, never refused), the checklist, and us-100.6's optional `proposed_version`/`version_rationale`. **us-101.3: every case needs `steps` and an `expected_result`, and every included work item must be accounted for — by a case, by one it already carries, or by `uncovered`.** All failures are returned at once. Supersedes `submit_release_run`, which no longer exists | `releases.notes_summary/notes_detail/notes_doc`; `test_cases` rows (agent-authored + inherited); a `release-notes-<version>.md` document rendered from the declaration; `release_prep_runs.status` → `succeeded`; **the UAT deploy fires immediately after** | Release prep: the whole hand-back |
| `trigger_deployment` | Starts the claimed deploy run's real deployment — rails re-checked independently of dispatch (protected refused always; production needs the flag) (US-13.13) | New `deployment_runs` row, attributed to the agent | Deploy run: first action |
| `get_deployment_run_status` | The triggered deployment run's status + log tail | Nothing — read-only | Deploy run: poll until finished |
| `get_deployment_health` | Runs the deployment's configured health check from the target server. On an `external` deployment it answers a stated *not applicable* rather than an error, so the agent stops instead of retrying (US-50.3) | Nothing — read-only | Deploy run: verify before any verdict (factory only) |
| `trigger_deployment_rollback` | The pre-authorized rollback — only once, only on an observed failure; refused without dispatch-time authorization, and **refused outright on an `external` deployment**, which has nothing to put back | New rollback `deployment_runs` row | Deploy run: failed health checks, when authorized |
| `submit_deploy_run` | Completes a `deploy` run with a verdict validated against what happened: deployed / deployed-unhealthy (managers notified) / rolled-back | `runs.status` → `succeeded`; verdict recorded; notification on unhealthy | Deploy run: last action |
| `get_run_status` | Where a run stands in plain vocabulary: queued, claimed, in review, approved, rejected (with feedback), merged, or failed; a rejected run names its retry | Nothing — read-only | After submitting, or to check a retry another worker carried |
| `get_pr_status` | The GitHub side of a submitted code run: PR state, mergeability, CI checks, unresolved review comments | Nothing — read-only | While waiting on review, to proactively fix a red check |
| `list_my_work` | The runs currently held (with lease expiry) plus recent submissions with an unsettled outcome | Nothing — read-only | Recovery view after a session restart |
| `submit_learning` | Contributes a discovery to the project's learnings; queues for the manager's review — the curated LLM merge runs at approval | New `learning_submissions` row | Any time a durable discovery is worth passing to the next run |
| `recommend_guideline_change` | **us-100.5**: proposes a revised **Agent Instructions document** (the full AGENTS.md body — sections retired with us-100.1) with a rationale and a self-declared severity — advisory only; a proposal identical to the current document is refused | New `guideline_recommendations` row keyed `agents` / `AGENTS.md`; the manager sees a diff and decides via `decide_guideline_recommendation`, which writes `projects.agent_instructions` and leaves the project unpublished | Any time working guidance looks wrong or stale |
| `submit_guidelines_refresh` | **us-100.5**: hands back a `guidelines` run as **whole files** — `files: [{key, proposed_text, rationale, severity}]`, `key` = `agents` (AGENTS.md) or a run kind (its `.buildmill/*.md`; a path is accepted too). Refuses an unknown key, a duplicate file, a file identical to what the project holds, a per-task file when the run's scope is `document`. An empty list is a legal "nothing to propose" | `guideline_refreshes` row updated (`pending` with rows, `decided` when empty); one `guideline_recommendations` row per file (`section_id` null, `section_title` = the repo path); `runs.status` → `succeeded`. The manager decides the pass **whole** with `decide_guidelines_refresh` — accept writes `projects.agent_instructions` / `worker_instructions` and leaves the project **unpublished** (us-99.4) | Guidelines run complete; the brief (`get_work_context`) carries the current document and every per-task file, read live |
| `release_work` | Hands a claim back to the pool with an optional note | `runs.worker_id` cleared; `issues.status` forced to `queued` | The run's state doesn't match what was expected — see the note on this at the end of [Lifecycles](#lifecycles) |
| `report_merge_failure` | **us-98.5**: ends a merge that cannot be finished, naming the branch that defeated it, the conflicting paths and what was tried. Refuses a branch outside the run, a blank branch, and a blank attempt — the text reaches the retry verbatim as feedback, so what it omits the next agent rediscovers | Run completes `failed` with the report as its error and `fault_class = work-fault` (code that genuinely conflicts is the work's problem, not the machine's, and must not count against runner health); the chore returns to a redispatchable state | A merge run hits a conflict it cannot resolve. Before this, an MCP agent could only `release_work` silently — a real conflict looked like an agent that wandered off |

### Runner WebSocket

One persistent JSON-RPC 2.0 socket per connected supervisor runner (`GET /runner/socket`,
`apps/api/app/routers/runner_socket.py`), opened with a `runner.hello` handshake carrying the
worker token. It is not how work is pulled — that's still the HTTP pool above — and browsers never
open it.

| Direction | Message | Meaning |
|---|---|---|
| Runner → server | `runner.hello` | Opening handshake: host info, agent versions, available modules. Server replies with a `session_id` and the runner's server-side config |
| Runner → server | `heartbeat` | Keeps `runner_sessions` presence alive |
| Runner → server | `llm.infer` | Brain inference relay — the supervisor reasons through the server's Vault-keyed provider with no model key on the machine; server replies with the completion |
| Runner → server | `gateway.mint` | Requests a short-lived scoped LLM Gateway key for a run/route; server replies with the key a CLI module will use |
| Runner → server | `command.audit` | Policy check before the runner executes a shell command; server replies allow/deny plus the reason, and records a `runner_command_audit` row |
| Runner → server | `command.result` | Reports a command's exit code and output after it ran; server finishes the matching audit row |
| Server → runner | `config.update` | Pushed live when a manager saves runner config (`PATCH /runner/{worker_id}/config`) while the runner is connected |
| Server → runner | `command.run` | A manager-initiated command (e.g. a manual repair) from `POST /runner/{worker_id}/command`, policed the same as a self-initiated one |

### A run's work items (`run_items`)

A run usually covers one work item — `runs.issue_id`. In `build_mode = 'feature'` the
**coding** phase covers several: one agent holding every story in the feature, one branch,
one commit, one PR, one review, one merge (US-22.9). Planning stays per story.

- `run_items(run_id, issue_id, org_id, position, prev_issue_status)` records the membership in
  `sub_no` order. A single-story run has **no** rows — absence means `runs.issue_id` is the
  whole membership. `prev_issue_status` (US-27.1) is where an unlanded or cancelled story goes
  back to.
- `run_item_commits(run_id, issue_id, commit_sha, …)` records **which commit landed which
  story's work** (US-27.1). Append-only, read-only to clients, and the evidence every
  downstream status decision reads: a run that claims six stories and commits four moves
  four. `run_coverage(run)` is the join to use.
- `run_issue_ids(run)` resolves both shapes, and is what claim, hand-back, approve, reject
  and test-result scoping all join to. There is one code path, not two.
- The code run attaches to the **feature**; its stories move together — `running` on claim,
  `merged` on approval, `needs-fixes` on rejection. On hand-back (US-27.1) only the stories
  with a **landed commit** reach `in-review`; the rest return to `prev_issue_status` with a
  `returned-to-pool` event, and the run's own issue moves with its members instead of sitting
  at `queued` behind its children. A failure is still shared by every member.
- **Committing and finishing are two different acts** (US-27.1). On a multi-story run,
  `submit_changeset` requires `issue_ids` (display id or uuid) and an explicit `final`; only
  `final=true` closes the run, and closing with an uncommitted member is refused unless
  `allow_partial=true`. Single-story runs are byte-for-byte unchanged.
- **One commit means one decision.** A batch is approved or sent back whole; the manager
  cannot approve stories 1–3 and reject 4–5. `story` mode is the answer for per-story gates.
- Dispatch **refuses** the code phase while any non-abandoned story lacks an approved plan,
  naming how many. A run covering only the planned stories would be held forever by a story
  outside it.
- **Embedding caveat.** `run_items` has foreign keys to both `runs` and `issues`, which is
  the shape PostgREST auto-detects as a junction table. That makes `runs → issues` (and
  `issues → runs`) **ambiguous**: a client `select=...,issues(...)` on `runs` fails with
  `PGRST201` and the surface silently renders empty. Always disambiguate from the client:
  `issues!runs_issue_org_fk(...)`. Server-side SQL is unaffected.
- `get_work_context` renders one section per story with its acceptance criteria inline;
  each story's approved plan is a separate `get_context_detail(run_id, "plan:<display-id>")`
  pull, which refuses a display id outside the run.

### The repo docs tree (`docs/factory/`)

What the factory writes into the project's own repository, and the query surface an agent
is expected to use. Written by `apps/api/app/repo_docs.py` — one writer, one direction,
never read back.

**Paths are work-item ids, never titles** (US-22.2), so retitling moves nothing:

```
docs/factory/index.json        every item, in build order — machine-readable
docs/factory/INDEX.md          the same list, for humans
docs/factory/README.md         what this tree is and who owns it
docs/factory/us-4.1/prd.md     a feature's approved PRD
docs/factory/us-4.1/us-4.2.md  a story in that feature
```

**Every generated `.md` opens with YAML front matter** (US-22.3), and `index.json` is an
array of the same records plus each entry's `path`. Every key is always present; absent
values are explicit `null`, never omitted.

| Key | Meaning |
|---|---|
| `id` | work-item display id, e.g. `US-4.2` |
| `issue_id` | the uuid, for MCP calls |
| `type` | `story` or `feature` |
| `title` | the title as approved |
| `parent` | the feature this story belongs to; `null` for a feature |
| `epic` | epic number |
| `order` | position in build order (epic → item no → sub no) |
| `has_plan` / `has_test_plan` | whether an approved plan is in this file |
| `merge_commit` | the latest approved code run's sha, or `null` |
| `generated_at` | when the tree was written — per sync, not per file |

**What appears, and when.** A feature appears once its PRD is approved. A story appears
once it is **dispatched** (US-22.4) — before planning, carrying the requirement only with
`has_plan: false` — so the backlog ahead of an agent is visible rather than materialising
one story at a time. A merged story gains an `## Outcome` section naming the merge commit,
the PR (omitted under the `main` direct strategy, rather than rendering a dead link), the
files changed and the agent's hand-back notes (US-22.5), derived from stored run data on
every rebuild.

**Ownership.** The tree is regenerated wholesale; anything under `docs/factory/` that a
generation stops producing is **deleted** (US-22.1), so everything found there is current.
Hand-added files there do not survive. Mutable state — status, assignee, comments, run
history — stays in Supabase.

### The wireframe tree (`docs/wireframes/`)

What the factory writes when an agent draws a story (US-48.1–48.5). A second
root, deliberately outside `docs/factory/`: that tree is regenerated wholesale
and US-22.1 deletes anything a generation stops producing, so two roots with
two writers is what keeps either from deleting the other's files.

```
docs/wireframes/README.md        what this tree is and who owns it
docs/wireframes/index.html       every wireframe, grouped by feature
docs/wireframes/us-4.2.html      one story's wireframe
docs/wireframes/_kit/kit.css     the styling every wireframe renders through
docs/wireframes/_kit/kit.js      the renderer
docs/wireframes/_kit/tokens.css  this project's design tokens
docs/wireframes/_kit/VERSION     the kit hash the repo is holding
```

**Paths are work-item ids, never titles** (US-22.2's rule), so retitling moves
nothing. A `no UI surface` verdict produces **no page** — it is named in the
index instead.

**A page is a declaration, not a document.** Each `.html` file is a small JSON
block in `<script type="application/wireframe+json">` that `_kit/kit.js`
renders into components named for the app's own — `card`, `table`,
`status-badge`, `empty-state`, `tabs`, `dialog`, `field`, `button`. Reading a
wireframe is meant to tell a coder which component to reach for. The full
declaration format is documented at the top of `kit.js`.

**Three constraints hold everywhere and are each one edit away from breaking:**
a page opens from disk with **no network** and no server; `kit.js` is a
**classic script, never a module** (a browser refuses to load a module over
`file://`); and every colour resolves through `tokens.css`, which is generated
from the *target project's* own stylesheet, never Build Mill's.

**The artifact is the source of truth; the file is a copy.** `artifacts.kind`
`wireframe` stores the declaration, at status `approved` — there is no gate.
That is what lets a kit upgrade restyle a whole repository's wireframes without
re-running one agent, and what lets a plan run be handed the *declared screens*
rather than 40 KB of HTML.

**Ownership.** The tree is regenerated wholesale by
`POST /projects/{id}/wireframes/sync`; anything under `docs/wireframes/` that a
generation stops producing is deleted. Hand-added files do not survive.

### The instruction files (`AGENTS.md`, `CLAUDE.md`)

The factory owns a **marker-fenced region** of both files and owns nothing outside it
(US-22.6). Content outside `<!-- buildmill:instructions:start -->` / `:end` survives every
write byte-for-byte; a file with no markers gets the block appended, never a replacement.

- One block, composed of the project's assembled guidelines plus the docs-tree section,
  written by exactly one code path. Pressing **Save instructions** and approving a plan
  produce byte-identical files.
- `CLAUDE.md` that is absent, empty, or exactly `@AGENTS.md` stays the pointer. One with
  other content keeps it and gains the fenced block.
- The block teaches the tree's addressing scheme, its front-matter keys, `index.json` as
  the one-read index, and that a code run already has the tree on disk from its workspace
  (US-22.8). The baked `plan` and `code` worker instructions say the same thing.
- Freshness is automatic (US-22.7): dispatch compares a sha256 of the block against
  `projects.instructions_synced_hash` and writes only when it differs — no GitHub call at
  all when it matches. A failure leaves the hash untouched so the next dispatch retries,
  and never blocks the run being created.

### Git proxy

`apps/api/app/routers/gitproxy.py` is a streaming smart-HTTP proxy in front of GitHub — it stores
no repo data itself; GitHub stays the source of truth. A worker authenticates with HTTP Basic auth
(the worker token as the password); the factory then attaches the org's own GitHub credential
(App installation token or PAT) before forwarding upstream, so that credential never reaches the
worker's machine.

**Fetch (`git-upload-pack`)** is capability-gated the same way `claim_work` is: a project outside
an allow-listed worker's list answers `404`, identical to a cross-org repository.

**Push (`git-receive-pack`)** is policed before a byte reaches GitHub, by one of two mutually
exclusive rail sets depending on whether the pushing principal holds a Power Git grant on the
project:

- **No grant (the default path)** — every ref update must be a branch-create or forward move on the
  branch that the pusher's own `running` claim actually names (`branch_ref`, matched by the legacy
  `refs/heads/factory/issue-<uuid>` naming too, for in-flight runs). Deletions, other branches, and
  history rewrites (the pushed-from head doesn't match the last head the factory recorded) are
  refused with a readable git `ERR` line. A push with no matching claim is refused outright — "claim
  the work item first."
- **A Power Git grant (`git_power_grants`, us-9.19)** lifts the claimed-run requirement entirely and
  substitutes four independently configurable rails, each a plain boolean on the grant: whether tag
  pushes, branch deletion, direct pushes to the default branch, and force-push are allowed. Because
  a pass-through proxy has no object graph to detect a true non-fast-forward, the force-push rail is
  enforced the same way the claim path detects a rewrite — against `git_power_branch_heads`, the
  last head the factory itself recorded for that branch.

What a principal can never do through this proxy, grant or no grant: delete or rewrite history on a
branch outside those rails, or see the org's real GitHub credential — it is attached upstream and
never echoed into a response, an error, or a log line.

### LLM Gateway

`apps/api/app/routers/llm_gateway.py` is a single catch-all route
(`POST/GET /api/v1/llm-gateway/{path}`) that a coding-agent CLI module's own provider SDK is
pointed at, via `ANTHROPIC_BASE_URL`/`OPENAI_BASE_URL`/etc. env vars the runner injects
(`module_env`). Since US-27.8 the key also carries the **model** the runner was configured with, and the gateway
resolves the provider from it — the configured provider whose `models` contains that id — because
`runner_code`/`runner_plan` are not keys in `LLM_FUNCTIONS` and route nowhere. A key with no model
(the brain, and any key minted before that) takes the old route-then-org-default path, and says so
in the run trace. It authenticates by a short-lived, per-run/route scoped key minted over the Runner
WebSocket's `gateway.mint` method — never a worker token, and never the org's real provider key.
On each call the gateway resolves the org's configured provider for the key's `route` (the same
US-3.17 routing `llm.infer` uses), reads the real key from Vault, attaches it in the auth
header the target provider expects, and streams the request through and the response back
untouched. Nothing is stored — this is a stateless relay, not a service holding its own state —
and it is a distinct path from `llm.infer`, which serves the supervisor's own reasoning rather
than a CLI module's calls.

