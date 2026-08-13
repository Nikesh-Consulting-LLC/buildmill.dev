# QA — what runs, and what it protects

A holistic view of the automated checks in this repository: what exists, which
suite it belongs to, and what class of failure each group was written to catch.

**Totals (2026-08-11):** 2,389 automated tests — 2,037 api (156 files), 273
runner (22 files), 79 web (12 files) — plus four non-test gates.

| Gate | Command | Cost | When |
|---|---|---|---|
| **api — Essential** | `npm run test:api` | ~30s · 1,538 tests | after every change |
| **api — Full QA** | `npm run test:api:full` | ~30min · +499 tests | before a release; migrations, RPCs, RLS |
| **runner** | `npm run test:runner` | ~80s · 273 tests | after runner changes |
| **web** | `npm run test:web` | <1s · 79 tests | after web changes |
| **types + build** | `npm run build` | ~15s | before committing web changes |
| **lint** | `npm run lint` | ~20s | before committing web changes |
| **stories** | `python scripts/story.py check` | instant | before committing story changes |
| **migrations** | `python scripts/migrate.py apply` | — | with every migration (both projects) |

The split is explained in [CLAUDE.md → Testing](CLAUDE.md#testing). In short:
Essential blocks outbound network and holds back only what needs a real Postgres;
Full QA runs everything. Nothing is excluded for being unimportant.

---

## 1. Access, isolation and secrets

The rules that must hold or one customer sees another's work.

- **Org isolation & RLS** — `capabilities_sql`, `fail_closed_grants`,
  `workers_sql`, `platform_admin_status_sql`, `agent_pools_sql`,
  `agent_servers_sql`, `slugs_sql`. Every table is org-scoped through
  `is_org_member`; these prove a second org sees nothing.
- **Authentication & capability** — `auth`, `admin_users`, `admin_orgs`,
  `suspend_reactivate_sql`, `agent_rename`. Includes the six-role matrix and
  that a suspended principal's tokens actually stop working.
- **GitHub credential scoping** — `github_org_scoping`, `github_connections`,
  `github_tokens`. A credential never crosses an org boundary, and connect binds
  to the workspace you are in.
- **Secrets stay write-only** — `storage`, `claude_subscription_sql`,
  `claude_connect`. Keys go to Vault or the private bucket; nothing reads them
  back.
- **Git proxy rails** — `git_proxy`, `git_proxy_integration` *(Full QA)*,
  `create_branch`. Who may push where, and that a refusal reads as a refusal.

## 2. The work pipeline

A run is claimed once, does one thing, and hands back honestly.

- **Pool, claim and lease** — `worker_pool`, `worker_pool_sql` *(Full QA)*,
  `lease_and_staleness`, `reaper_sql`, `run_liveness_sql`, `dispatch`,
  `dispatch_issue_sql`, `issue_dispatch_blocks_sql`. One winner per claim,
  expired claims requeue, an unchecked kind is never offered.
- **Hand-back** — `handback_sql`, `refused_handback`, `handback_test_case_shapes`,
  `test_plan_case_shapes`, `changesets`, `changeset_ignore`, `plan_callback`.
  A submission survives the agent's formatting, and a refusal never loses work.
- **Run lifecycle** — `run_cancel`, `reset_run_sql`, `run_hold_reason_sql`,
  `run_attempts`, `dispatch_resets_attempts`, `session_resume`,
  `db_complete_run`, `run_billing_stamp`. Includes the attempts ceiling and the
  invariant that "still live" is always an allow-list, never a deny-list.
- **Review and approval** — `reviews`, `escalation_and_overrides`,
  `auto_approve_sql`, `feature_batch_order_sql`, `feature_phase_sql`,
  `branch_coverage`, `workflow`. Nothing merges on an agent's own authority.

## 3. Agents, the runner and the fleet

- **Provisioning** — `agent_provision` (72 tests), `agent_servers`,
  `agent_pools`, `agent_pool_recovery`, `auto_repair_sql`,
  `orphan_cleanup_sql`. Install strategy per module, per-slot users and 0600 env
  files, capacity and quota, self-heal.
- **The control socket** — `runner_socket`, `socket_handler_guards`,
  `runner_policy`, `runner_incidents`, `idle_reason`, `agent_idle_reasons`,
  `runner_kind_coverage`, `admin_run_config`. Config validation, platform-owned
  fields, and why an agent is idle.
- **Interactive agent (ACP)** — `interactive_agent`, `agent_sessions`,
  `run_console`, `ws_accept`; runner-side `acp_client`, `acp_handlers`,
  `interactive_module`, `session_process`, `session_host`. The protocol framing,
  the file-access rail, the console, and sessions with no work item.
- **Runner internals** — `workloop`, `modules`, `cli_modules`,
  `module_declarations`, `settings_delivery`, `progress_stream`,
  `opencode_stream`, `repair`, `time_ceiling`, `no_credentials_refusal`,
  `workspace`, `workspace_prepare`, `mcp_agent`.

## 4. Work items, planning and prompts

- **Hierarchy and ordering** — `epics_numbering_sql`, `breakdown_dispatch_sql`,
  `elaborate_story`, `wireframe_run`, `wireframe_kit`,
  `wireframe_dispatch_sql`, `work_unit_scaling`, `complexity`, `complexity_sql`.
- **What an agent is told** — `run_settings`, `run_settings_delivery`,
  `presets`, `presets_routes`, `prompt_templates`, `prompt_templates_sql`,
  `guidelines_refresh`, `guideline_recommendations_sql`, `projects_learnings`,
  `projects_save_instructions`, `instruction_set_sql`,
  `worker_instructions_sql`, `buildmill_section_sql`, `build_config`,
  `project_environment_sql`.
- **The repo docs tree** — `repo_docs`, `documents`, `documents_sql`.

## 5. Integrations

- **Worker MCP** — `factory_mcp` (172 tests, *Full QA*), `mcp_tools`,
  `workspace_delta`, `clarifications_sql`, `issue_comments_sql`. The surface an
  agent actually works through.
- **GitHub** — `github`, `github_module`, `github_issues_pull`,
  `merge_diagnosis`. Including a merge failure naming its own cause.
- **LLM routing** — `llm`, `llm_learnings`, `llm_deploy_script`,
  `validation`.

## 6. Money and metering

`llm_gateway`, `metering`, `spend_breakdown`, `cache_token_pricing`,
`admin_usage`, `backfill_metrics_sql`. The gateway tees every stream into
`llm_usage`; parsed-vs-unparsed is never conflated with zero, and a subscription
run is deliberately off-meter rather than a gap.

## 7. Releases and deployment

`releases`, `release_branches_sql`, `release_delete_sql`, `deployments` (93
tests), `external_deployments`, `deploy_runs_sql`, `branch_deploy_pipeline`,
`deployment_website_sql`, `guidelines_ready_release_sql`,
`worker_instructions_release_sql`. The immutable release, the pinned commit, and
that promotion never re-versions.

## 8. Truth, errors and self-monitoring

The group that exists because this system reports on itself.

- **Self-reporting** — `self_reporting`, `app_issue_ingestion`, `app_issues_sql`,
  `operational_reporting`. Which failures are defects, and that a report never
  carries credentials.
- **Failing well** — `supabase_errors`, `supabase_unreachable`,
  `client_disconnect`, `handler_scopes`, `ws_accept`, `request_timing`,
  `repair_evidence`, `run_trace_sql`, `run_activity_sql`. A hang-up is not a
  crash; a handler must survive every scope it serves; a failure leads with what
  the tool actually said.
- **Analytics** — `run_analytics`, `performance_summary`, `metrics`,
  `user_activity_and_gate_latency`, `activity_feed_sql`, `tldr`,
  `content_audit`, `run_coverage`, `test_runs_sql`, `agent_test_results_sql`,
  `agent_failures_sql`.

## 9. Invariant guards — the tests that watch the code

Not feature tests. Each derives its expectation from the schema or the source,
so the next change that breaks the rule fails here instead of in production.

| Guard | Derives from | Catches |
|---|---|---|
| `embed_ambiguity` | generated types | a new FK making an un-hinted PostgREST embed answer 300 |
| `console_columns` | generated types | a query naming a column that does not exist (the console shipped broken this way) |
| `run_cancel` (scan) | `db.py` source | a deny-list status predicate letting a new terminal status leak into the pool |
| `handler_scopes`, `socket_handler_guards` | source sweep | an error handler that assumes an HTTP scope |
| `ws_accept` | source sweep | a bare `accept()` outside the safe helper |
| `module_declarations`, `runner_kind_coverage` | runner + api | a run kind or module known to one side and not the other |
| `agent-roles.test.ts` | `runner_socket.py` on disk | a dispatchable kind with no role in the UI |
| `agent-modules.test.ts` | `agent-runner-data.ts` | the offered agent types changing without a deliberate edit |
| `suite_split` | conftest rule | the network guard silently dying and the suite going back to 30 minutes |

## 10. Web

79 tests, all pure logic: `agent-roles`, `agent-modules`, `work-items`,
`epic-picker`, `stage-tracker`, `stage-durations`, `pool-availability`,
`dispatch-block`, `error-classify`, `normalize-route`, `factory-git`,
`app-version`. `npm run build` type-checks the rest.

---

## What this does not cover

Stated plainly, because a green run should not be read as more than it is.

- **No CI gate.** By decision. The local run *is* the gate, which is why
  Essential has to be fast enough to actually run.
- **No component or browser tests.** Web coverage is pure functions only —
  nothing asserts that a page renders, a form submits, or a socket reconnects in
  a real browser. `npm run build` proves it compiles, not that it works.
- **The SQL/RLS layer is untested without `DATABASE_URL`.** Those tests skip
  themselves, so on a fresh checkout Full QA and Essential cover the same ground
  and every RLS assertion above is silently absent.
- **The interactive agent is unproven end to end.** Its ACP conversations are
  tested against a scripted double; no test drives the real CLI.
- **The runner is not covered by the deploy pipeline**, so a green suite here
  says nothing about what is running on a pool machine.
- **Two standing manual checklists** — [Full App Browser QA](stories/us-Full-App-Browser-QA.md)
  and [Full Git Router QA](stories/us-Full-Git-Router-QA.md) — cover ground the
  automated suites do not. They are run on demand, by a human.
