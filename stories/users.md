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
confirmed and collapsed on 2026-08-11, Phases 79–90 (41 stories) on 2026-08-13, and
Phases 91–96 (42 stories) were closed on 2026-08-15, and **Phases 98–102 (27
stories) on 2026-08-16** — all built and released to production, with the manager
testing on live rather than through per-story UAT sittings. The condensed record —
including what those phases did *not* prove, the manager actions each left open
(96.10's routing table, 96.11's worker-token rotation), the five acceptance criteria
that closed unbuilt (98.6's structured review table, 99.4's publish audit, 99.6's
seed-publishes-files and section preview, 99.7's accept/decline, 100.1's
`project_guidelines` drop migration), the Phase 78 known gaps, and the
retired-unbuilt-do-not-re-propose list — is in
[APPLICATION.md → Delivery history](../APPLICATION.md#delivery-history).

Thirteen stories are open: Phase 103's release deadlock (built and released
2026-08-16), Phase 104's two pieces of work that existed nowhere safe,
Phase 97's GitHub linkage repair (requested 2026-08-15, costing live runs
today), and the residue carried out of Phases 85–89:

| Order | Story | Title | Status |
|---|---|---|---|
| 1 | [us-103.1](us-103.1-an-abandoned-release-prep-is-reaped.md) | An abandoned release prep is reaped | Testing |
| 2 | [us-103.3](us-103.3-the-manager-can-stop-an-in-flight-release.md) | The manager can stop an in-flight release | Testing |
| 3 | [us-103.2](us-103.2-a-restarted-runner-re-adopts-its-prep.md) | A restarted runner re-adopts the prep it was holding | Testing |
| 4 | [us-103.4](us-103.4-the-workbench-shows-the-release-in-flight.md) | The Workbench shows the release in flight | Testing |
| 5 | [us-103.5](us-103.5-a-release-in-flight-freezes-dispatch.md) | A release in flight freezes dispatch, and says so | Testing |
| 6 | [us-104.1](us-104.1-every-migration-number-is-unique.md) | Every migration number is unique | Testing |
| 7 | [us-104.2](us-104.2-the-api-test-suite-lives-in-the-repo.md) | The API test suite lives in the repo | Testing |
| 8 | [us-97.1](us-97.1-a-moved-repo-relinks-or-asks.md) | A moved repo relinks itself, or asks | New |
| 9 | [us-85.3](us-85.3-a-broken-machine-is-not-a-work-fault.md) | A broken machine is not a work fault | New |
| 10 | [us-87.9](us-87.9-every-foreign-key-has-its-index.md) | Every foreign key has its index | New |
| 11 | [us-87.8](us-87.8-logs-age-out.md) | Logs age out, diffs live outside the row | New |
| 12 | [us-87.10](us-87.10-a-page-load-has-a-budget.md) | A page load has a budget | New |
| 13 | [us-89.3](us-89.3-grok-settings-ride-the-managed-scope.md) | The agent's Grok settings ride the managed scope | New |

**Phase 103 — A release cannot get stuck** (drafted 2026-08-16, from the
2026.08.16.3 incident). The runner restarted ten minutes into preparing a
release. Its supervising task died; the `release_prep_runs` row did not. Two
and a half hours later the prep was still `running`, its lease 26 minutes
expired, the worker online and healthy — and nothing anywhere was ever going
to change that. Release prep is the one claimed job in the factory with **no
lease reaper**: `requeue_expired_claims` sweeps `runs` only, and nothing reads
`release_prep_runs.claim_expires_at`. The manager had no move either — `/cancel`
takes only `queued`, `/retry` only `failed`, and `running` sits between them
with an empty Actions cell — while `releases_one_in_flight_per_project` counted
the dead release as in flight and blocked every future cut for the project. It
was cleared by editing the production database. us-103.1 reaps an expired prep
lease down the failure path that already exists, so Retry becomes reachable
by itself; us-103.3 gives the manager a Stop that ends the job as well as the
release (and closes the matching hole in `/reject`, where a zombie agent could
still write notes onto a rejected release); us-103.2 has a restarted runner
re-adopt the prep it is holding, so a routine restart costs a minute rather
than a release. The two surfaces the manager asked for follow: us-103.4 gives
the Workbench release card the same liveness reading story runs already get —
who holds it, how long, whether it is silent or abandoned, and Stop — because
for two and a half hours that card cheerfully read "being prepared"; and
us-103.5 freezes `plan`/`code` dispatch on a project while its release is in
flight, through migration 235's existing `issue_dispatch_refusal` so the button
and the RPC cannot disagree. Writing stories, bugs and chores stays open;
routing them waits for the release to be released, stopped or rejected, and
every surface says so in those words.

**Phase 104 — The tree can be trusted** (drafted 2026-08-16). Two pieces of
real work were found existing nowhere safe, and both are the same failure in
different clothes. **us-104.1**: `main` carried five duplicated migration
numbers (014, 015, 205, 249, 271), which makes "applied in numeric order"
ambiguous at each one. A fix for the 249 collision had been *written* on
2026-08-13 — renumbering the file, plus an Essential guard so the next
collision fails in a test rather than on somebody's replay, plus a real bug it
uncovered in `migrate.py` whose skip-if-applied check was dead for 92 of 258
prod rows — and then it sat on an unpushed local branch, went stale (it
renumbered to 252; `252_agent_effort_ledger` landed on main the next day), and
was deleted in a branch cleanup on 2026-08-16. It was recovered from the
object store, brought current at 276/277, and merged. **us-104.2**: the
Playwright API suite in `scripts/testing/` — 22 specs over 227 operations,
whose auth-boundary layer asserts that a *forged but well-formed* credential
is refused, not merely that a missing one is — had been untracked since
2026-08-15 with its own `.gitignore` already written for the commit that never
came. Both stories exist because a guard that lives on one laptop is not a
guard.

**Phase 97 — GitHub linkage stays true** (drafted 2026-08-15, from the
run-`ff9ef2be` incident). A repository rename/transfer on GitHub answers
REST calls with `301 Moved Permanently`; the factory's client neither
follows nor names it, so every MCP hand-back tool on the Demo project failed
with stringified `KeyError`s (`'sha'`, `'commit'`) while the git proxy —
git follows redirects — pushed fine, and the worker parked on a
clarification with finished work in hand. us-97.1 makes the REST client
redirect-aware through one shared helper, stops broken payloads from
leaking as riddles, and closes the loop the manager asked for: a detected
move **relinks the project automatically** when the GitHub App can see the
repo at its new path (audited, org notified), and **asks** — a named
broken-link state on the project page, plain words in every tool answer —
when it can't. The Edit dialog also stops pretending a stale path is
selectable when the installation no longer offers it.

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
