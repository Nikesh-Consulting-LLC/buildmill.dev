_Part of the [application reference](../../APPLICATION.md) — the index, audience guide, and rules & invariants live there. Keep this file current in the same commit as the change it describes._

## Domain objects

These are the objects an agent needs to be able to name to operate or develop the factory. Each
is a real Supabase table (`apps/web/src/lib/supabase/database.types.ts` is the authoritative
inventory); everything else in the schema is a join, an audit trail, or config that supports one
of these without needing its own name in conversation.

| Object | Is | Belongs to | Key fields an agent cares about |
|---|---|---|---|
| Organization | The tenant boundary; everything else is org-scoped | — | id, shortname |
| Project | A product plus its linked GitHub repo | Organization | repo_full_name, default/uat/production branch, slug |
| Epic | A numbered grouping of issues, the unit a release version is cut from | Project | number, status, active |
| Issue | The unit of work — `type` defaults to `story`; also `feature`, `bug`, `chore`; can nest via `parent_id` | Epic (and Project directly) | type, status, acceptance_criteria, assignee_id |
| Run | One provider's attempt at an issue — claims it, produces a branch and PR | Issue | status, provider, worker_id, pr_url, claim_expires_at |
| Approval | The manager's gate decision on an issue (optionally a narrower subject, e.g. a run) | Issue | gate, decision, actor, comment |
| Test case | A reusable UAT check, exercised across test runs and scored per run | Project (optionally an issue) | status, steps, expected_result |
| Release version | A cut, tagged version bundling an epic's issues | Epic (and Project directly) | version, git_tag, included_items |
| Deployment | A configured deploy target: one project on one server | Project · Server | environment, branch, strategy, protected |
| Server | A registered SSH destination host — a deploy target, an agent machine, or both | Organization | host, port, auth_method |
| Agent server | A machine Build Mill installs, runs, updates and retires coding agents on (US-26.1) | Organization · Server (1:1) | status, workdir, modules, bundle_hash, probe readout |
| Agent slot | One agent on that machine: its own identity and its own systemd service | Agent server | slot_index, worker_id, principal_id, desired_state, service_state |
| Principal | The unified identity behind any action — human or agent | — (org membership is a separate join, see below) | kind, active_org_id, auth_user_id |
| Worker | A registered external agent/tool credential that can claim runs over Worker MCP | Organization (optionally linked to a Principal) | type, status, principal_id, token_last4 |

```
organization
├── project
│   ├── epic
│   │   ├── issue ──► run ──► approval
│   │   └── release version
│   ├── test case
│   └── deployment
├── machine (deploy target · agent host · both)
│   └── agent slot ──► worker · principal
├── principal (kind: human · agent)
└── worker
```

Machines and principals are org-scoped, not project-scoped, contrary to what the tree's
indentation might suggest: `servers` carries only `org_id`, and a **factory** `deployment` is the
join that pairs a project with a server — the arrow from project to deployment above elides that
second parent. An **external** deployment (US-50.1, `deployments.kind`) has no server at all: it
belongs to the project alone, and deploying it means merging `branch` into `target_branch` on
GitHub, with nothing on any machine. `server_id` and `target_folder` are therefore nullable, and
a check constraint (`deployments_kind_shape`) requires them for `factory` and forbids a server
for `external`. The kind is chosen at creation and never edited — a history half SSH transfer and
half merge would mean nothing. Principals aren't project-scoped either: org membership goes through the separate
`organization_members` join (so a principal can belong to more than one org), and
`principals.kind` is a label on one identity table, not a three-way table split — `workers` is a
distinct credential registration that optionally points back to a principal. A run does not
cascade into a deployment through a foreign key: `deployment_runs.promoted_from_run_id` /
`redeploy_of_run_id` / `rollback_to_run_id` are plain UUID columns with no declared relationship,
so deploying is a separate, explicitly triggered action against a project+server pair, not an
automatic continuation of an approved run.

**Supporting tables** — exist so a developing agent knows they're there; they don't get their
own row above.

- `organization_members` — join between an organization and a principal, carrying `role` and `status`
- `profiles` — one row per Supabase Auth user, backing a `human`-kind principal
- `role_capabilities` — which capabilities each role is allowed
- `issue_comments`, `issue_events` — discussion thread and state-change log on an issue
- `clarifications` — a question a run asks mid-flight and the manager's answer
- `artifacts` — versioned text output a run produces on an issue (`plan`, `test_plan`, `prd`, `elaboration`, `wireframe`). Every kind but `wireframe` lands at `draft` behind a gate; a wireframe lands at `approved` because US-48.2 deliberately has none
- `documents` — uploaded files attached to a project, issue, run, or test case
- `test_runs`, `test_run_results` — a UAT session and its per-test-case pass/fail
- `deployment_runs` — one execution of a deployment (status, commit, log, artifact)
- `deployment_run_events`, `deployment_events`, `deployment_notifications`, `deployment_env_vars` — a deployment run's timeline, the deployment's own timeline, alert routing, and its environment variables
- `git_power_grants`, `git_power_grant_events`, `git_power_branch_heads` — the Power Git escape-hatch grant on a project+principal, its audit trail, and the branch heads it's pushed
- `github_connections` — an org's connected GitHub App installation or PAT
- `llm_providers`, `llm_function_routes`, `llm_prompt_templates` — configured thinking-task LLM providers, which function routes to which, and prompt overrides
- `notification_endpoints`, `notifications` — where alerts get delivered, and per-principal delivery records
- `project_build_config`, `project_guidelines`, `guideline_recommendations` — a project's build/gate config, its written guidelines, and worker-proposed edits to them
- `project_learnings`, `learning_submissions` — a project's accepted learnings, and worker-submitted candidates awaiting a decision
- `worker_instructions` — per-project, per-run-kind instructions baked into a worker's prompt
- `worker_capabilities`, `worker_capability_events` — the US-13.10 capability matrix: one row per
  (worker, project, capability) grant over seven named stages (`prd`, `breakdown`, `plan`, `code`,
  `test`, `release`, `deploy`). **US-31.3: the gate is fail-CLOSED — zero rows means the worker is
  offered nothing, can claim nothing, and can clone nothing.** (It previously meant *unrestricted*,
  which let a freshly provisioned agent claim work in and read the repository of every project in
  the org.) All three gates — pool listing, claim, and the git-proxy read gate — call the one
  predicate `public.worker_has_grant(worker, project, capability)`; a null capability asks "any
  grant on this project", which is the clone gate. The events table logs per-capability
  `granted`/`revoked`; rows written by US-31.3's migration carry `actor = 'migration/backfill'`
- `run_attempts` — US-31.5's append-only log of consumed attempts against a work item (issue, run,
  **worker id**, kind, reason ∈ `failed` | `lease-expired` | `heartbeat-stale` | `ceiling`).
  Deliberately not derived from `runs`: a lease requeue mutates the run row back to `queued` and
  nulls `worker_id`, so run rows cannot answer "how many times has this agent tried". A per-agent
  cap (`runner_config.max_item_attempts`) stops one agent; an org ceiling
  (`organizations.max_item_attempts`) latches `issues.attempts_blocked_at`, after which a BEFORE
  INSERT trigger on `runs` refuses every dispatch path until the manager releases it. A
  `cancelled` run consumes nothing
- `workspace_deliveries` — US-31.6: per (worker, project), the sha and path manifest the factory
  last served, so `get_workspace` can answer a delta with **explicit deletions** instead of a whole
  tree. Any ambiguity (rewritten history, unknown status, size ceiling) answers `full` instead
- `runner_config`, `runner_sessions`, `runner_incidents`, `runner_command_audit` — the supervisor runner's config, live connection presence, self-repair incidents, and command audit trail.
  US-31.2 adds `runner_config.max_run_minutes` (nullable; null = the worker-type default lease),
  which `claim_run` and `extend_claim` both honour, and a stale-heartbeat sweep requeues a
  supervisor-managed claim silent for 90s with a note distinct from a lease expiry
- `agent_server_jobs` — every SSH-side action on an agent server (`provision`, `add_slot`,
  `update`, `restart`, `remove_slot`, `teardown`, `probe`) with a redacted, streamed log.
  One queued/running job per host is enforced by a partial unique index
- `content_audit` — before/after log of edits to guideline/instruction text
- `activity_feed` (a view, not a table) — a unified cross-object feed for the UI

