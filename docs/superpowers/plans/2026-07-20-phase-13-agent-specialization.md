# Phase 13 — Agent Specialization & Effectiveness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement all 12 open Phase 13 stories (us-13.2 → us-13.13) on branch `claude/phase-13-implementation-84adfc`, in the build-sequence order from `stories/users.md`, leaving every story at `Testing` for the user's UAT.

**Architecture:** Two strands share one subject. *Effectiveness* (13.2–13.9): give runs repo access resolved from the project row, a guaranteed hand-back notes channel, a factory-owned docs tree in the customer repo, a pull-based compact work context, liveness/stall detection, live progress surfaces, a self-explaining runner console, and a headless-worker setup guide. *Specialization* (13.10–13.13): re-model `worker_capabilities` as row-per-grant over seven stages with generic `capability = kind` enforcement, then land three new run kinds (`test`, `release` — which carries the project-scoped-runs schema, `deploy` — with safety rails).

**Tech Stack:** FastAPI + psycopg (apps/api), FastMCP (factory_mcp.py), Supabase Postgres migrations (infra/supabase/migrations, applied live via MCP `apply_migration`, project `wdudmfhhqxrqzoyhuzwx`), Next.js 16 + Base-UI shadcn (apps/web), pytest, `npm run build`.

## Global Constraints

- Every migration file `NNN_name.sql` is **also applied to the live Supabase project** in the same story, then `apps/web/src/lib/supabase/database.types.ts` regenerated (MCP `generate_typescript_types`). Next free number: **107**.
- `baked_worker_instruction` migrations **always start from the current definition (106) and carry every kind's text verbatim** — the 095/105/106 lesson. Never rebuild from an older body.
- Every new table/column keeps org-scoped RLS; `db.py` queries filter `org_id` explicitly (service conn bypasses RLS).
- No frontend/UI testing by me — user UATs. Verification = `pytest` (apps/api) + `npm run build` (apps/web).
- One commit per story minimum; story file `**Status:**` → `Testing` + `users.md` row updated in the same commit as the story's code.
- Statuses: only the user moves a story past Testing. Never mark Completed.
- Zero-capability-rows = unrestricted worker; first row flips to allow-list. Preserved verbatim through 13.10.
- Secrets never surface: deploy-run context must not carry credentials or env values; test asserts response shape (13.13).
- shadcn here is Base UI (`render={<Button/>}`, `items` on Select), `StatusBadge`/`EmptyState` shared components.

## Key codebase facts (from exploration, verified 2026-07-20)

- `factory_mcp.py` (2460 L): `_held_run_and_token` L873–901 reads `ic["repo_full_name"]` only → us-13.2 root cause. `get_work_context` L489–864 inlines everything incl. full `ic` in `out.context` → us-13.5 target. Submit tools funnel into `routers/worker.perform_submit`; only code transports take `notes` (becomes PR body). Release instruction served as reference on code runs at L778–785.
- `db.py` (2479 L): pool predicate L705–719 and claim predicate L895–906 OR-chain `can_plan`/`can_code`; git gate = `worker_allowed_for_project` L913–933 (project-level, any row). `claim_run` L936 (leases: human 24h / autonomous 15min, `_LEASES` L317). `extend_claim` L1076; `record_progress_note` L1102 → `progress-note` event. `release_claim` L1130 (has `note`); `force_requeue_run` L1035; `requeue_expired_claims` L1176 (startup + lazily before pool listing — **no timer**). `complete_run` L37 (kind-branched issue status/events). Known gap: release/expiry forces `issues.status='queued'` regardless of kind — must be fixed before `test` runs exist.
- `runs`: status domain frozen (`queued/running/succeeded/failed`), kind check currently `('plan','code','prd','breakdown')` (085), `issue_id` NOT NULL composite-FK (031), claim cols (040), `pushed_head_sha` (041).
- `worker_capabilities` (043): PK (worker_id, project_id), `can_plan`/`can_code` bools, `check(can_plan or can_code)`; audit trigger `log_worker_capability_change` (granted/updated/revoked).
- `issue_events.type` is free-form (no CHECK).
- Dispatch RPCs: `dispatch_prd_draft` (049, **no repo keys**), `dispatch_breakdown` (085, **no repo keys**), `dispatch_issue` (104 v7, includes `repo_full_name` + `default_branch`).
- `runner_config` (100): `enabled_modules text[]`, `model_routes jsonb`, `concurrency 1–16`, `autonomy_policy jsonb`. Console UI = 4 bare inputs in `team/[principalId]/runner/page.tsx` `ConfigEditor` L293–399; saves via `PATCH /api/v1/runner/{worker_id}/config`.
- Review surface `review/[issueId]/page.tsx` branches PRD gate L71–116 / plan gate L119–188 / code gate L190–417; `review-actions.tsx` holds Approve/Reject.
- Work item page `issues/[id]/page.tsx`: `eventDetail` L69–113 already renders progress-note text (shipped half of 13.7); `StageTrackerCard` at L470–479; natural slot for latest-note callout right after it.
- Dashboard `dashboard/data.ts`: capability reach L615–625, `claimable()` L627–632, `StalledQueue` L634–654; `AGENT_STATUSES` L18; progress notes not read at all.
- Deployment run panel `run-panel.tsx` L324–345 Run dialog; POST `/api/v1/deployments/{id}/run` in `run-deployment-dialog.tsx` L77–79; `protected` badge page L174–179.
- Releases page `projects/[id]/releases/page.tsx` `CutReleaseButton` L112; `cut_release_version` RPC (082) is the only version authority.
- `changesets.apply_changeset` = blobs→tree→commit→ref writer to reuse for docs commits; `github.py` has `create_pull`, `compare_commits`, git-data primitives.
- Team Connect: `team-view.tsx` tab body L244–254 → `connect-panel.tsx` (`isAgent` branch L208–212, `AgentInstructions` L217–250); snippets from `settings/worker-connect.ts`.
- Help: `help_content.py` — text-as-data.
- apps/web runs a modified Next.js — read `node_modules/next/dist/docs/` before assuming App Router APIs.

---

### Task 1: us-13.2 — PRD and breakdown runs can read the repository

**Files:**
- Modify: `apps/api/app/factory_mcp.py` (`_held_run_and_token` L873–901; `get_work_context` prd L557–593 / breakdown L595–639 branches)
- Modify: `apps/api/app/db.py` (`get_worker_run` select — add `p.repo_full_name as project_repo_full_name` if absent)
- Modify: `apps/api/app/repo_browse.py` (no signature change; callers pass an effective ic)
- Test: `apps/api/tests/test_factory_mcp.py` (or the file where repo-tools tests live)

**Interfaces:**
- Produces: `_held_run_and_token` resolves `repo_full = ic.get("repo_full_name") or run.get("project_repo_full_name")`, builds `ic_eff = {**ic, "repo_full_name": repo_full, "default_branch": ic.get("default_branch") or run.get("default_branch") or "main"}` used for `resolve_ref`. No migration (fix-at-tool-call, cannot drift).

**Steps:**
- [x] Failing test: breakdown-kind run (ic without repo keys) + project row with repo → `get_repo_tree` succeeds (mock `github.get_tree`); older-run compatibility (plan run with ic keys still works).
- [x] Failing test: error triage — repo unreachable vs ref-not-found vs empty repo. Empty repo (no branches) → success-shaped answer "repository has no files yet", not an error; bad explicit ref with other branches present → "ref '<ref>' not found"; credential failure → unreachable wording.
- [x] Implement: db.py select addition; `_held_run_and_token` fallback; `get_repo_tree` catch-path probing (`github.get_repo` → reachability; `github.list_branches` → emptiness; else ref-not-found), distinct messages.
- [x] `get_work_context` prd/breakdown branches: add `## Repository` markdown section + `repo_full_name`/`default_branch` structured keys + a "read it before writing" line; only when a repo is linked.
- [x] Run pytest for apps/api → green. Commit `feat(mcp): prd and breakdown runs can read the repository (us-13.2)` + story → Testing + users.md.

### Task 2: us-13.3 — An agent can always reach the manager

**Files:**
- Create: `infra/supabase/migrations/107_handback_notes.sql` (`alter table runs add column handback_notes text;`) — apply live, regen types
- Modify: `apps/api/app/routers/worker.py` (`Submit` model + `perform_submit`)
- Modify: `apps/api/app/factory_mcp.py` (`submit_plan`/`submit_prd`/`submit_stories` gain `notes_for_manager: str = ""`; code tools document that `notes` reaches the manager; `get_instructions` output gains a standing channel footer)
- Modify: `apps/web/src/app/(app)/review/[issueId]/page.tsx` (+ small `agent-notes.tsx` banner component) — all three gates show run.handback_notes
- Test: `apps/api/tests/test_handback_sql.py` / `test_factory_mcp.py`

**Interfaces:**
- Produces: `Submit.notes` persists to `runs.handback_notes` on every successful submit **and** posts an attributed `issue_comments` row via the existing worker-comment path (thread survival). Existing PR-body behavior for code notes kept.

**Steps:**
- [x] Migration 107 written + applied live + types regenerated.
- [x] Failing test: submit_plan with notes → runs.handback_notes set, issue_comments row exists attributed to worker; submit without notes unchanged.
- [x] Implement perform_submit persistence + comment insert; MCP params; get_instructions footer ("Hand-back notes: pass notes_for_manager on any submit_* — flagging concerns is part of finishing the work; it cannot be blocked by a denied tool").
- [x] Review surface: notes banner ("What the agent wants you to know") in PRD/plan/code gate branches reading the relevant succeeded run.
- [x] pytest green; `npm run build` green. Commit `feat(worker): hand-back notes always reach the manager (us-13.3)` + story/users.md.

### Task 3: us-13.4 — Approved work lands in the repo, owned by the app

**Files:**
- Create: `infra/supabase/migrations/108_repo_docs_tree.sql` (`alter table projects add column docs_tree_enabled boolean not null default false;` + `baked_worker_instruction` v-next from 106: all kinds verbatim, plan/code texts gain one sentence pointing at `docs/factory/INDEX.md` when it exists) — apply live, regen types
- Create: `apps/api/app/repo_docs.py` (layout, index generation, AGENTS.md section, commit machinery)
- Modify: `apps/api/app/routers/workflow.py` (`prd/approve`, `plan/approve` → fire-and-log docs write)
- Modify: `apps/api/app/routers/projects.py` (`POST /projects/{id}/docs-tree/sync` — scaffold/rebuild/retry)
- Modify: web project settings surface (toggle → enables + calls sync)
- Test: `apps/api/tests/test_repo_docs.py`

**Interfaces:**
- Produces: `repo_docs.write_feature(settings, project, issue)` / `write_story(...)` / `sync_tree(settings, project)`; `commit_docs(token, repo_full, branch, message, files: dict[str,str])` (head-based git-data commit, one retry on ref race). Layout: `docs/factory/README.md`, `docs/factory/INDEX.md`, `docs/factory/<display-id-lower>-<slug>/prd.md` + `<story-display-id-lower>-<slug>.md`. AGENTS.md marked section `<!-- buildmill:docs-tree -->`.
- Rule: writes only when `docs_tree_enabled`; only approved artifacts; failure → `issue_event 'docs-write-failed'` + surfaced warning, approval unaffected.

**Steps:**
- [x] Failing tests: layout/slugs/index ordering pure functions; approval succeeds when GitHub write raises; sync rebuilds from approved artifacts only.
- [x] Implement module + hooks + endpoint + toggle; migration applied; types regenerated.
- [x] pytest + build green. Commit `feat(api): approved work lands in the repo docs tree (us-13.4)` + story/users.md.

### Task 4: us-13.5 — The work context stops flooding the worker

**Files:**
- Modify: `apps/api/app/factory_mcp.py` (`get_work_context` all branches; new read-only tool `get_context_detail(run_id, section)`)
- Test: `apps/api/tests/test_factory_mcp.py` (+ size-ceiling test)

**Interfaces:**
- Produces: brief keeps — id/kind, story + AC, branch & hand-back mechanics, instructions template + instruction set, run commands + environment + build config (code), **plan + test_plan inline for code runs**, **PRD inline for breakdown runs**, feedback inline, test cases as `{id,title,status}` compact list. Everything else becomes named pointers: `omitted` structured list, each entry `{section, how}`. `out.context` (full ic echo) removed. New tool `get_context_detail(run_id, section)` with `section ∈ prd|plan|test_plan|guidelines|learnings|test_cases|discussion|documents|previous_prd` serving the frozen ic / live assemblers. Docs-tree paths mentioned when enabled; fallback is the tool (nothing depends on the tree).
- Size test: fixture with 30k PRD, 15k plan, 40 cases, 20k guidelines → `len(markdown) - len(plan) - len(test_plan) - len(story) - len(ac) < 4000` and structured out carries no prd/guidelines/learnings bodies.

**Steps:**
- [x] Failing size + omitted-manifest + pull-tool tests; implement; keep plan/code compat (older runs). pytest green.
- [x] Commit `feat(mcp): compact pull-based work context (us-13.5)` + story/users.md.

### Task 5: us-13.6 — Unattended runs cannot stall silently

**Files:**
- Create: `infra/supabase/migrations/109_run_liveness.sql` (`alter table runs add column last_heartbeat_at timestamptz; update runs set last_heartbeat_at = claimed_at where worker_id is not null;` + activity_feed view v-next including `claim-expired` / `run-released` / `run-failed` events) — apply live, regen types
- Modify: `apps/api/app/db.py` (`claim_run`/`extend_claim` stamp `last_heartbeat_at = now()`; `requeue_expired_claims` payload gains worker name + held-minutes; **kind-guard the forced `issues.status='queued'`** in `release_claim`, `requeue_expired_claims`, `force_requeue_run` — only `plan`/`code` kinds touch issue status, closing the documented prd/breakdown gap before `test` runs exist)
- Modify: `apps/api/app/main.py` (60s asyncio sweep task: requeue_expired_claims + reconcile_pushed_expired_claims)
- Create: `POST /runs/{run_id}/force-requeue` (manager JWT) in `apps/api/app/routers/reviews.py` or `issues.py` → `force_requeue_run`
- Modify: `apps/web/src/app/(app)/dashboard/data.ts` + `page.tsx` (+ components): "Runs needing attention" — silent-but-claimed (last_heartbeat_at older than 2× autonomous lease or 30 min for human claims) with "Requeue now"; recently-died items (claim-expired / run-released-with-note / run-failed events) with plain reason
- Modify: `apps/api/app/factory_mcp.py` `release_work` docstring (giving up with a reason is the honest path)
- Test: `apps/api/tests/` liveness + kind-guard + force-requeue tests

**Steps:**
- [x] Failing tests: heartbeat stamping; sweep task registration; kind-guard (releasing a prd/breakdown claim leaves issue status untouched); force-requeue endpoint auth + effect.
- [x] Implement; migration applied; types regenerated; dashboard data additions.
- [x] pytest + build green. Commit `feat(factory): unattended runs cannot stall silently (us-13.6)` + story/users.md.

### Task 6: us-13.7 — See what the agent is doing while it does it

**Files:**
- Create: `infra/supabase/migrations/110_narration_instructions.sql` (`baked_worker_instruction` v-next from 108's definition: plan/code texts ask for `report_progress` notes at meaningful boundaries; all kinds verbatim) — apply live
- Modify: `apps/web/src/app/(app)/issues/[id]/page.tsx` — "Live activity" callout after StageTrackerCard: latest progress-note text, elapsed-running, last-heard-ago; realtime on issue_events INSERT; degrades to nothing without notes
- Modify: `apps/web/src/app/(app)/dashboard/data.ts` + rows — in-flight items show last note snippet + freshness (reuse 13.6 computation)
- Modify: `apps/web/src/app/(app)/team/team-view.tsx` Live tab — headless MCP workers with active claims appear (worker + current claim + last-heard), not just socket runners
- Test: build only (web-heavy); pytest still green.

**Steps:**
- [x] Implement data plumbing (runs.claimed_at/last_heartbeat_at + latest progress-note event) into issue page load and dashboard; UI slots; Live tab claims query.
- [x] Migration applied. Build green. Commit `feat(web): live agent progress and liveness (us-13.7)` + story/users.md.

### Task 7: us-13.8 — The runner console explains itself

**Files:**
- Modify: `apps/api/app/routers/runner_socket.py` — PATCH config validates `autonomy_policy` patterns (`re.compile` each; 422 with the offending pattern), `enabled_modules`/`concurrency`/`model_routes` shapes; new `POST /runner/{worker_id}/policy-preview {command}` → `runner_policy.evaluate` verbatim → `{decision, matched_pattern}`; response includes a `changed` field naming what differs
- Rework: `apps/web/src/app/(app)/team/[principalId]/runner/page.tsx` (`ConfigEditor` → lead text; module checkboxes with one-line descriptions, unavailable-with-reason from session host info; concurrency select 1–16; model-routes table over prd/breakdown/plan/code/test/release/deploy + brain with org-model dropdowns + inherit-default option; autonomy radio (allow / require-approval / deny) with plain-consequence copy, `{}` legible as **allow**; pattern list editors with client-side `new RegExp` validation; live policy preview box; save reports what changed + push status; unknown stored keys preserved and surfaced)
- Non-runner honesty: worker without runner type/config/sessions → explanatory panel, no dead console
- Test: `apps/api/tests/` — pattern rejection, preview endpoint parity with `runner_policy.evaluate`.

**Steps:**
- [x] Failing API tests → implement validation + preview; rework UI; pytest + build green.
- [x] Commit `feat(runner): console explains itself, validated policy editing (us-13.8)` + story/users.md.

### Task 8: us-13.9 — A setup guide that gets a headless agent working first time

**Files:**
- Modify: `apps/web/src/app/(app)/settings/worker-connect.ts` (new headless snippet kind: MCP JSON config, headless invocation with per-flag explanations, connection-check command, allow-list block)
- Modify: `apps/web/src/app/(app)/team/connect-panel.tsx` (agent branch gains Supervisor runner ↔ Headless MCP worker toggle; token-shown-once warning at the reveal)
- Modify: `apps/api/app/help_content.py` (+ the /help page consuming it): same material as reference — worker loop in order, choosing worker types, allow-list trap with full per-kind tool list, auth for unattended use (`claude setup-token` → `CLAUDE_CODE_OAUTH_TOKEN`), troubleshooting (401 / empty pool / missing tool / expired auth / expired lease)
- Test: build; help content unit test if pattern exists.

**Steps:**
- [x] Implement snippets + panel + help content (post-13.3 wording: submit notes cannot be silenced). Build green.
- [x] Commit `feat(web): headless worker setup guide (us-13.9)` + story/users.md.

### Task 9: us-13.10 — Capability matrix: staff agents by stage

**Files:**
- Create: `infra/supabase/migrations/111_capability_matrix.sql` — new row-per-grant `worker_capabilities` (`capability` check in the seven names, `unique(worker_id, project_id, capability)`, composite FKs per 043 pattern, RLS mirrored from 043), backfill (`can_plan`→`prd`+`breakdown`+`plan`, `can_code`→`code`), audit trigger v2 (granted/revoked, per-capability detail), drop old shape — apply live, regen types
- Modify: `apps/api/app/db.py` — pool L705–719 and claim L895–906 predicates collapse to `wc.capability = r.kind`; `worker_allowed_for_project` reads new shape (unchanged meaning)
- Rework: `apps/web/src/app/(app)/workers/[id]/capabilities.tsx` — matrix: project rows × seven capability chips; Test/Release/Deploy marked "no dispatchable work yet"; zero-rows/first-row semantics + warnings preserved; both hosts (worker page + Team drawer) get it free
- Modify: `apps/web/src/app/(app)/dashboard/data.ts` L615–632 — per-kind `claimable(kind)` = `capability === kind`
- Test: rework `apps/api/tests/test_capabilities_sql.py` — behavior-preservation (four kinds × granted/absent, before/after mapping), zero-rows unrestricted, first-row flip, git gate unchanged, cross-org isolation, audit events.

**Steps:**
- [x] Failing tests against new predicates; migration written + applied; db.py + UI + dashboard; types regenerated.
- [x] pytest + build green. Commit `feat(capabilities): seven-stage capability matrix (us-13.10)` + story/users.md.

### Task 10: us-13.11 — Test runs: staffed verification

**Files:**
- Create: `infra/supabase/migrations/112_test_runs.sql` — `runs.kind` +`'test'`; `worker_instructions.run_kind` +`'test'`; `baked_worker_instruction` v-next from 110 adding the `test` contract text (all kinds verbatim); seed `test` instruction rows for existing projects (095 pattern) — apply live, regen types
- Modify: `apps/api/app/db.py` — `dispatch_test_run(settings, issue_id, org_id, actor)` (guards: submitted code run with branch exists; no queued/running test run for the issue; freezes `branch_ref`, repo, test cases, run commands pointer into input_context; event `test-run-dispatched`); `complete_run` gains `test` branch (no issue-status change, event `test-run-completed`); `report_test_results` accepts test-kind runs
- Modify: `apps/api/app/routers/workflow.py` — `POST /issues/{issue_id}/test-run/dispatch`
- Modify: `apps/api/app/factory_mcp.py` — `get_work_context` `test` branch (branch ref, read-only checkout via factory remote, run commands, build config, full test cases); new `submit_test_run(run_id, summary, stdout="")` (rejects zero reported results with release guidance); `validate_submission` knows test runs carry no changeset
- Modify: `apps/web/src/app/(app)/review/[issueId]/` — "Send for verification" in the code gate once a submission exists (re-offered after new pushes); results render through existing TestStateStrip (no new UI)
- Test: dispatch guards; capability gating (`kind='test'` offered/withheld via 13.10 predicate); zero-results rejection; context shape.

**Steps:**
- [x] Failing tests → migration → db/mcp/router → web button. pytest + build green.
- [x] Commit `feat(runs): staffed test runs (us-13.11)` + story/users.md.

### Task 11: us-13.12 — Release runs: agent-prepared release cuts

**Files:**
- Create: `infra/supabase/migrations/113_project_scoped_runs.sql` — `runs.project_id` (backfill from issues, NOT NULL, composite FK; BEFORE INSERT trigger fills from `issue_id` so existing dispatch RPCs stay untouched); `issue_id` DROP NOT NULL + `check (issue_id is not null or kind in ('release'))`; kind +`'release'`; activity_feed view v-next covering issue-less runs (named by project) — apply live, regen types
- Modify: `apps/api/app/db.py` — every runs consumer handles `issue_id null`: `list_worker_pool` (LEFT JOIN issues, project via `r.project_id`), `worker_allowed_for_run` (capability project from `r.project_id`), `claim_run` (no issue writes when null), `get_worker_run` (LEFT JOIN), `complete_run` (`release` branch), `release_claim`/`requeue_expired_claims`/`force_requeue_run` (already kind-guarded by 13.6); `dispatch_release_run(settings, project_id, org_id, actor)` (guards: unreleased release_records exist; no concurrent release run; freezes records, strategy, branches, computed next version); `unreleased_release_records` + `peek_next_version` helpers (version math mirrors `cut_release_version` — read-only, never mints)
- Modify: `apps/api/app/routers/projects.py` — `POST /projects/{project_id}/release-run/dispatch`
- Modify: `apps/api/app/factory_mcp.py` — `get_work_context` `release` branch; `submit_release_run(run_id, notes_markdown, open_promotion_pr=False, stdout="")` — rejects notes whose title lacks the computed version; creates the release-notes project document; when strategy has a UAT branch and default is ahead (`compare_commits`), opens the default→UAT PR server-side (`github.create_pull`) and records it on `runs.pr_url`; `validate_submission` release case
- Modify: `apps/web/src/app/(app)/projects/[id]/releases/page.tsx` — "Prepare release" beside CutReleaseButton (disabled without unreleased records / while one is in flight)
- Test: issue-less runs across pool/claim/context/completion; version-mismatch rejection; cross-org isolation; capability gating.

**Steps:**
- [x] Failing tests → migration → consumers sweep → dispatch/context/submit → web. pytest + build green.
- [x] Commit `feat(runs): project-scoped release runs (us-13.12)` + story/users.md.

### Task 12: us-13.13 — Deploy runs: agent-executed deployments with rails

**Files:**
- Create: `infra/supabase/migrations/114_deploy_runs.sql` — kind +`'deploy'`; issue-null check extends to `('release','deploy')`; `runs.deployment_id uuid` composite FK (set null on delete); `deployments.agent_dispatch_allowed boolean not null default false` + config-audit trigger event (027 pattern); `worker_instructions.run_kind` + baked instruction +`'deploy'` (v-next from 112, all kinds verbatim, seed rows) — apply live, regen types
- Modify: `apps/api/app/routers/deployments.py` — `POST /deployments/{deployment_id}/agent-dispatch {ref?, auto_rollback}` (refuses: protected; production without the flag; concurrent deploy run); shared trigger logic factored so the MCP path reuses the exact human-run path
- Modify: `apps/api/app/db.py` — `dispatch_deploy_run`, deploy-run context/completion helpers
- Modify: `apps/api/app/factory_mcp.py` — claim-scoped tools `trigger_deployment(run_id)` (re-checks protection + flag independently), `get_deployment_run_status(run_id)` (status + log tail), `get_deployment_health(run_id)`, `trigger_deployment_rollback(run_id)` (only pre-authorized, once, only on failed health); `submit_deploy_run(run_id, verdict ∈ deployed|deployed-unhealthy|rolled-back, summary)` — verdict validated against observed deployment-run state; unhealthy → existing notifications; context carries definition/ref/rollback-authorization and **no credential or secret** (test asserts key set)
- Modify: `apps/web/src/app/(app)/projects/[id]/deployments/[deploymentId]/` — "Dispatch to agent" beside Run (hidden when protected; production needs the flag); settings toggle for `agent_dispatch_allowed`
- Test: rails end-to-end (protected refused at dispatch AND trigger AND rollback), flag gating, single rollback, verdict validation, context secret-free shape, capability gating.

**Steps:**
- [x] Failing rails tests → migration → dispatch/tools/verdicts → web. pytest + build green.
- [x] Commit `feat(runs): agent-executed deploy runs with rails (us-13.13)` + story/users.md.

### Task 13: Finalize

- [x] Full `pytest` (apps/api) + `npm run build` from clean state.
- [x] All 12 story files at `Testing`, users.md table synced, APPLICATION.md updated for new tools/endpoints/kinds (it is the authoritative catalog and now drifts: +`get_context_detail`, +`submit_test_run`/`submit_release_run`/`submit_deploy_run` + deploy observation tools, new run kinds, capability matrix, force-requeue endpoint, docs-tree endpoints).
- [x] Push branch; summarize for user UAT (note the 13.10 live-migration window: prod still reads can_plan/can_code until released).

## Self-review notes

- Spec coverage: every AC in 13.2–13.13 maps to a step above; 13.7's first AC is already shipped on main (eventDetail) — remaining ACs covered in Task 6.
- The 13.6 kind-guard fix is deliberately sequenced before 13.11 (test claims must not knock in-review issues to queued).
- Instruction-text migrations: 108 (docs pointer) → 110 (narration) → 112 (+test) → 114 (+deploy), each starting from the previous definition.
- 13.12's BEFORE INSERT trigger avoids rewriting three dispatch RPCs; Python dispatchers still set project_id explicitly.
