# Software Factory — User Stories Index

Open work is one file per story (`us-N.M-slug.md`) in this folder; this file is the index.
New stories take the next free `N.M` (numbers are stable, never reused) and get slotted into
the build sequence where they'll be worked.

Statuses: `New` (written) → `Testing` (built, awaiting the manager's UAT) → `Completed`.
Only the manager moves a story past `Testing`. When a phase closes, its story files are
deleted and the essence lands in [APPLICATION.md → Delivery history](../APPLICATION.md#delivery-history);
git history keeps the full text.

## Open work

The 2026-08-09 backlog close confirmed everything built to that point (62 phases,
479 stories); Phases 73–75 followed on 2026-08-10, Phases 76–78 (22 stories) were
confirmed and collapsed on 2026-08-11, and **Phases 79–90 (41 stories) were confirmed
on live and collapsed on 2026-08-13**. The condensed record — including what those
phases did *not* prove, the Phase 78 known gaps, and the
retired-unbuilt-do-not-re-propose list — is in
[APPLICATION.md → Delivery history](../APPLICATION.md#delivery-history).

Twenty-one stories are open, all drafted but unbuilt — Phase 91's usability work
first (requested 2026-08-13), then the residue carried out of Phases 85–89:

| Order | Story | Title | Status |
|---|---|---|---|
| 1 | [us-91.1](us-91.1-the-tab-is-named-for-what-you-do-there.md) | The tab is named for what you do there | Testing |
| 2 | [us-91.2](us-91.2-dispatch-opens-with-what-is-already-running.md) | Dispatch opens with what is already running | Testing |
| 3 | [us-91.3](us-91.3-an-agent-at-work-has-a-door-to-its-cli.md) | An agent at work has a door to its CLI | Testing |
| 4 | [us-91.4](us-91.4-things-to-do-groups-by-project.md) | Dispatch groups by project, and a project folds away | Testing |
| 5 | [us-91.5](us-91.5-the-status-filter-is-a-set-not-a-choice.md) | The status filter is a set, not a single choice | Testing |
| 6 | [us-91.6](us-91.6-the-test-library-is-a-table-you-can-page.md) | The test library is a table you can page through | Testing |
| 7 | [us-91.7](us-91.7-reports-are-bug-reports-and-live-under-activity.md) | Reports are Bug Reports, and they live under Activity | Testing |
| 8 | [us-91.8](us-91.8-the-activity-feed-pages-ten-at-a-time.md) | The activity feed pages ten at a time | Testing |
| 9 | [us-91.9](us-91.9-a-project-card-says-which-build-is-live.md) | A project card says which build is live | Testing |
| 10 | [us-91.10](us-91.10-superadmin-is-four-menus-not-one-drawer.md) | SuperAdmin is four menus, not one drawer | Testing |
| 11 | [us-91.11](us-91.11-an-agents-work-is-measured-in-seconds.md) | An agent's work is measured in seconds | Testing |
| 12 | [us-91.12](us-91.12-the-team-page-opens-with-three-numbers.md) | The team page opens with three numbers | Testing |
| 13 | [us-91.13](us-91.13-a-count-on-a-coloured-button-must-be-readable.md) | A count on a coloured button must be readable | Testing |
| 14 | [us-91.14](us-91.14-every-item-shows-what-it-cost.md) | Every item shows what it cost | Testing |
| 15 | [us-91.15](us-91.15-a-notification-says-what-happened-and-goes-there.md) | A notification says what happened, and goes there | Testing |
| 16 | [us-91.16](us-91.16-the-build-stamp-names-the-build.md) | The build stamp names the build | Testing |
| 17 | [us-85.3](us-85.3-a-broken-machine-is-not-a-work-fault.md) | A broken machine is not a work fault | New |
| 18 | [us-87.9](us-87.9-every-foreign-key-has-its-index.md) | Every foreign key has its index | New |
| 19 | [us-87.8](us-87.8-logs-age-out.md) | Logs age out, diffs live outside the row | New |
| 20 | [us-87.10](us-87.10-a-page-load-has-a-budget.md) | A page load has a budget | New |
| 21 | [us-89.3](us-89.3-grok-settings-ride-the-managed-scope.md) | The agent's Grok settings ride the managed scope | New |

**Phase 91 — Usability: the dashboard reads like the job** (drafted 2026-08-13,
the manager's own list). Nothing here is broken; all of it is friction the
manager pays for daily. Things to Do's first tab is renamed **Dispatch** for the
act it exists for (us-91.1) and gains an **In Progress** section on top — only
work an agent has actually claimed, in the factory's own row shape, with the
roster's CLI-window button on each row (us-91.2, us-91.3) — and both of its
sections group by project with projects that fold away (us-91.4). Work Items
stops opening on a wall of finished work: the status filter becomes a checkbox
set with merged and done unchecked by default (us-91.5). The test library
becomes a paged table instead of a card list you scroll (us-91.6). Reports
becomes **Bug Reports** and moves down beside Activity (us-91.7), and Activity
stops asking which projects matter twice, paging ten rows at a time (us-91.8).
Finally, a project card stops answering "did the last deploy work" and starts
answering the question the page is opened with — which build is live on UAT and
on production, read from the run each deployment is actually serving (us-91.9).
The superadmin's one fifteen-link drawer becomes a SuperAdmin section with four
menus — Machines, Accounts, Settings, Logs (us-91.10). And the phase's one
piece of real plumbing: an agent's work becomes a measured quantity — seconds
recorded per run and rolled up daily beside the lines, tokens and dollars the
runs already carry (us-91.11) — so Team can open with three numbers and give
every agent its own totals (us-91.12). us-91.14 puts the dollars where the
decisions are made — what each finished run cost on Things to Do, and what each
work item has cost across every attempt on Work Items. us-91.15 makes the bell mean
something: every notification the API actually writes gets a renderer built from
its own payload, a destination, and repeat-collapsing — today they all read
"runner_fault: a work item" and click nowhere. us-91.16 makes the footer's build stamp
carry the commit and the build time rather than a tag-relative string that goes
stale without looking stale — and that now names no tag at all, the repository
having been re-created with none. us-91.13 is a legibility fix: a grey quota
count on an orange button.

**Phase 85 residue — us-85.3** (drafted 2026-08-12) closes the loop on the incident
that motivated us-85.1's workspace verification. A run that fails on a broken bench —
no usable shell, an unreachable or token-rejecting factory MCP, a corrupt workspace —
must be recorded `machine-fault`, **proven by re-running us-85.1's environment checks
after the failure**, not by grepping the transcript. That label matters to three
consumers: escalation (US-33.4, migration 161) climbs the preset only on work-fault,
precisely because a broken box is not answered by thinking harder; the US-68.3
auto-repair ladder needs the slot flagged even while its process is up; and a story's
failure history should blame the story only when the story deserves it. The defining
case is the US-2.8.1 plan run of 2026-08-12 (pool machine 9), a pure environment
failure later proven by us-85.1's own checks and fixed by a machine Update — yet
`runs.fault_class` recorded **work-fault**, and every consumer drew the wrong
conclusion.

**Phase 87 residue — the database layer** (drafted 2026-08-12, from
[docs/performance-analysis-2026-08-12.md](../docs/performance-analysis-2026-08-12.md)).
The application-side work shipped; what is left is underneath it, and it is the cheap,
mechanical kind that compounds quietly as the workspace grows.

- **us-87.9** — Supabase's performance advisor returns **169 findings** against prod:
  117 unindexed foreign keys (worst on `documents`, `app_issues`, `clarifications`,
  `guideline_refreshes`, `issue_comments`, `runs`, `test_cases`), 27 unused indexes,
  1 duplicate (`projects_id_org_key` vs `projects_id_org_unique`), 12 unwrapped
  `auth.*()` calls across 135 policies re-evaluating per row, and 20
  multiple-permissive-policy cases.
- **us-87.8** — nothing in this database is ever deleted. There is no retention logic
  in the API and **no `pg_cron` schedule in any migration**. Measured on prod:
  `api_request_log` 584,934 rows / **106 MB** (growing ~585k rows per six weeks),
  `content_audit` 36 MB, `runs` 185 rows / **33 MB** (one `diff` row is 30 MB).
  `client_perf_events` takes a browser-side insert on every page load and has the
  same shape.
- **us-87.10** — the budget, and the reason this phase does not decay the way the last
  one did. Performance decayed invisibly for months and every regression was introduced
  by a reasonable change: a badge that needed a count, a filter that needed a body, a
  subscription that needed to be live. Nothing failed; it just got slower and nobody
  was holding a number. The instrumentation already exists — `api_request_log`
  (US-62.8) and `client_perf_events` — and nothing reads it as a gate. This seeds a
  100-project / 5,000-item fixture and puts a budget on each surface. It depends on
  87.1–87.9 because it measures what they fix.

**Phase 89 residue — us-89.3** (drafted 2026-08-13) finishes the zero-secret workspace
at the config layer: the factory configures the interactive agent's CLI the way its
vendor documents rather than through ad-hoc files. Everything the factory ENFORCES —
the model block (gateway `base_url`, `env_key = "BUILDMILL_GATEWAY_KEY"`,
`api_backend`), the us-89.1 loopback broker's MCP entry, timeouts and hardening — goes
to the **managed scope** (`$GROK_HOME/.grok/managed_config.toml`, per
docs.x.ai/build/settings), with hard constraints in **requirements.toml** where the CLI
supports them. The user scope stays the agent's own, and the **project scope**
(`.grok/config.toml` inside the workspace repo) is never written by the factory again —
the layer us-83.1 had to harden against injection, and the file that carried a token
into a project repo on 2026-08-13, simply stops being ours. `grok inspect` — the
vendor's own "what configuration loaded" command — becomes the verification, run by
Prepare Workspace step 7 and the session doctor, so a config that silently failed to
load is a named failure rather than a mystery mid-run.

## Standing QA checklists

Not stories — reusable full-surface test scripts, run on demand:

- [Full App Browser QA](us-Full-App-Browser-QA.md)
- [Full Git Router QA](us-Full-Git-Router-QA.md)
