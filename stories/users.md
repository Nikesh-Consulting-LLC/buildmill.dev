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

Sixty-one stories are open — Phase 91's usability work first (requested
2026-08-13), then Phase 92's phone work, the public site and its beta gate
(requested 2026-08-14), Phase 95's cost management (requested 2026-08-14),
Phase 97's GitHub linkage repair (requested 2026-08-15, ahead of Phase 96
because it is costing live runs today), Phase 96's per-type work-item paths
and its runner-health addendum from the 2026-08-14 run-log analysis, then
Phase 98's merge kind and Phase 99's move of the instructions into the
repository (both requested 2026-08-15), and the residue carried out of
Phases 85–89:

| Order | Story | Title | Status |
|---|---|---|---|
| 1 | [us-91.1](us-91.1-the-tab-is-named-for-what-you-do-there.md) | The tab is named for what you do there | Completed |
| 2 | [us-91.2](us-91.2-dispatch-opens-with-what-is-already-running.md) | Dispatch opens with what is already running | Testing |
| 3 | [us-91.3](us-91.3-an-agent-at-work-has-a-door-to-its-cli.md) | An agent at work has a door to its CLI | Completed |
| 4 | [us-91.4](us-91.4-things-to-do-groups-by-project.md) | Dispatch groups by project, and a project folds away | Completed |
| 5 | [us-91.5](us-91.5-the-status-filter-is-a-set-not-a-choice.md) | The status filter is a set, not a single choice | Testing |
| 6 | [us-91.6](us-91.6-the-test-library-is-a-table-you-can-page.md) | The test library is a table you can page through | Testing |
| 7 | [us-91.7](us-91.7-reports-are-bug-reports-and-live-under-activity.md) | Reports are Bug Reports, and they live under Activity | Completed |
| 8 | [us-91.8](us-91.8-the-activity-feed-pages-ten-at-a-time.md) | The activity feed pages ten at a time | Completed |
| 9 | [us-91.9](us-91.9-a-project-card-says-which-build-is-live.md) | A project card says which build is live | Testing |
| 10 | [us-91.10](us-91.10-superadmin-is-four-menus-not-one-drawer.md) | SuperAdmin is four menus, not one drawer | Completed |
| 11 | [us-91.11](us-91.11-an-agents-work-is-measured-in-seconds.md) | An agent's work is measured in seconds | Completed |
| 12 | [us-91.12](us-91.12-the-team-page-opens-with-three-numbers.md) | The team page opens with three numbers | Completed |
| 13 | [us-91.13](us-91.13-a-count-on-a-coloured-button-must-be-readable.md) | A count on a coloured button must be readable | Completed |
| 14 | [us-91.14](us-91.14-every-item-shows-what-it-cost.md) | Every item shows what it cost | Completed |
| 15 | [us-91.15](us-91.15-a-notification-says-what-happened-and-goes-there.md) | A notification says what happened, and goes there | Completed |
| 16 | [us-91.16](us-91.16-the-build-stamp-names-the-build.md) | The build stamp names the build | Testing |
| 17 | [us-91.17](us-91.17-the-workspace-picker-travels.md) | The workspace picker travels, and says when it's switching | Testing |
| 18 | [us-91.18](us-91.18-merged-work-asks-to-be-released.md) | Merged work asks to be released | Testing |
| 19 | [us-91.19](us-91.19-the-workdesk-is-one-page.md) | The workdesk is one page | Testing |
| 20 | [us-92.1](us-92.1-the-tabs-fit-the-phone.md) | Things to Do fits in a hand | Testing |
| 21 | [us-92.2](us-92.2-one-filter-button-not-eleven-pills.md) | One filter button, not eleven pills | Testing |
| 22 | [us-92.3](us-92.3-a-release-is-a-card-with-its-buttons-showing.md) | A release is a card, with its buttons showing | Testing |
| 23 | [us-92.4](us-92.4-a-test-case-says-what-it-is.md) | A test case row says what it is | Testing |
| 24 | [us-92.5](us-92.5-the-report-is-the-row.md) | On Bug Reports, the report is the row | Testing |
| 25 | [us-92.6](us-92.6-a-project-card-leads-with-its-state.md) | A project card leads with its state | Testing |
| 26 | [us-93.1](us-93.1-the-front-door-tells-the-story.md) | The front door tells the story | Testing |
| 27 | [us-94.1](us-94.1-a-new-account-waits-at-the-door.md) | A new account waits at the door | Testing |
| 28 | [us-95.1](us-95.1-cost-gets-its-own-room.md) | Cost gets its own room, and managers hold the key | Testing |
| 29 | [us-95.2](us-95.2-spend-is-a-curve-not-a-number.md) | Spend is a curve, not a number | Testing |
| 30 | [us-95.3](us-95.3-every-dollar-names-the-work.md) | Every dollar names the work that bought it | Testing |
| 31 | [us-95.4](us-95.4-a-slice-narrows-and-a-view-travels.md) | A slice narrows, and a view travels | Testing |
| 32 | [us-97.1](us-97.1-a-moved-repo-relinks-or-asks.md) | A moved repo relinks itself, or asks | New |
| 33 | [us-96.1](us-96.1-a-chore-is-one-shot.md) | A chore is one shot | Testing |
| 34 | [us-96.2](us-96.2-a-bug-explains-itself-before-the-fix.md) | A bug explains itself before the fix | Testing |
| 35 | [us-96.3](us-96.3-every-kind-of-work-gets-its-own-words.md) | Every kind of work gets its own words | Testing |
| 36 | [us-96.4](us-96.4-the-feature-holds-the-steering-wheel.md) | The feature holds the steering wheel | Testing |
| 37 | [us-96.7](us-96.7-the-workbench-triages-the-feature.md) | The workbench triages the feature, not its stories | Testing |
| 38 | [us-96.5](us-96.5-the-buttons-match-the-work.md) | The buttons match the work | Testing |
| 39 | [us-96.6](us-96.6-a-failed-breakdown-gets-another-try.md) | A failed breakdown gets another try | Testing |
| 40 | [us-96.10](us-96.10-the-stage-shapes-the-model.md) | The stage shapes the model | Testing |
| 41 | [us-96.8](us-96.8-the-hand-back-speaks-with-one-voice.md) | The hand-back speaks with one voice | Testing |
| 42 | [us-96.9](us-96.9-a-stop-is-an-answer-not-a-breakdown.md) | A stop is an answer, not a breakdown | Testing |
| 43 | [us-96.11](us-96.11-a-key-never-rides-the-trace.md) | A key never rides the trace | Testing |
| 44 | [us-98.1](us-98.1-the-factory-learns-a-merge-run.md) | The factory learns a merge run | Testing |
| 45 | [us-98.2](us-98.2-a-merge-names-the-branches-it-will-land.md) | A merge names the branches it will land | Testing |
| 46 | [us-98.3](us-98.3-the-agent-reads-every-branch-it-must-merge.md) | The agent reads every branch it must merge | Testing |
| 47 | [us-98.4](us-98.4-a-merge-hands-back-a-branch-and-a-pull-request.md) | A merge hands back a branch and a pull request | Testing |
| 48 | [us-98.5](us-98.5-an-unresolved-branch-fails-the-whole-merge.md) | An unresolved branch fails the whole merge | Testing |
| 49 | [us-98.6](us-98.6-the-manager-reviews-the-merge-summary.md) | The manager reviews the merge summary | Testing |
| 50 | [us-99.1](us-99.1-every-instruction-kind-has-a-file.md) | Every instruction kind has a file, and one map says which | Testing |
| 51 | [us-99.2](us-99.2-agents-md-is-the-index.md) | AGENTS.md is the index, and Build Mill owns it whole | Testing |
| 52 | [us-99.3](us-99.3-project-conventions-become-guidelines-md.md) | Project conventions become Guidelines.md | Testing |
| 53 | [us-99.4](us-99.4-an-unpublished-edit-says-so.md) | An unpublished edit says so, and the manager pushes it | Testing |
| 54 | [us-99.5](us-99.5-the-agent-reads-the-file-and-mcp-fills-the-gap.md) | The agent reads the file, and MCP fills the gap | Testing |
| 55 | [us-99.6](us-99.6-a-template-carries-the-whole-file-set.md) | A template carries the whole file set | Testing |
| 56 | [us-99.7](us-99.7-a-template-edit-offers-itself.md) | A template edit offers itself to the projects using it | New |
| 57 | [us-85.3](us-85.3-a-broken-machine-is-not-a-work-fault.md) | A broken machine is not a work fault | New |
| 58 | [us-87.9](us-87.9-every-foreign-key-has-its-index.md) | Every foreign key has its index | New |
| 59 | [us-87.8](us-87.8-logs-age-out.md) | Logs age out, diffs live outside the row | New |
| 60 | [us-87.10](us-87.10-a-page-load-has-a-budget.md) | A page load has a budget | New |
| 61 | [us-89.3](us-89.3-grok-settings-ride-the-managed-scope.md) | The agent's Grok settings ride the managed scope | New |

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

**Phase 93 — The public site** (drafted 2026-08-14, the manager's request).
buildmill.dev today hands a visitor a login form; the public site behind it
(`apps/public`) is one fold and a three-card "how it works" — a placeholder
wearing the brand. us-93.1 replaces it with a single immersive page that
tells the story of Build Mill top to bottom: what it is in one repeatable
sentence, what it does as a journey (one story followed from written to
promoted, every human gate on the way), what the features are, and who it
is for — every claim true of the shipped product, zero dependencies, no
external requests, and the root domain actually landing there rather than
on the app's login.

**Phase 94 — The beta gate** (drafted 2026-08-14, the manager's request).
The public site now invites anyone in, and the app would let them straight
through. us-94.1 puts a superadmin nod between signup and the workspace: a
new account authenticates but waits at a gate page that says plainly the
product is in beta and every account is approved by hand; SuperAdmin →
Accounts gains the pending queue with per-row Approve; enforcement is
server-side (RLS/API, not a client redirect); every existing account is
grandfathered in the same migration; the decision is audited. No deny
flow, no emails, no invite codes — approval only.

**Phase 95 — Cost management** (drafted 2026-08-14, the manager's request).
Money today is one settings page every member can open, plus facts scattered
where us-91.14 put them. Phase 95 gives cost its own room and locks the
door: a top-level **Costs** section holding the spend reporting, gated to
owner and admin through a new `view_costs` capability in the US-9.2 grid,
with the rates form staying behind in Settings where configuration lives
(us-95.1). The section then learns the manager's actual questions: which way
spending is going — a daily curve and a window-over-window comparison, every
figure still computed from the append-only `llm_usage` ledger (us-95.2);
what the money bought in the work's own terms — spend grouped by work-item
type, epic, and item via the run → issue join, with money that cannot be
attributed shown as a named bucket rather than dropped or pro-rated
(us-95.3); and how to narrow a view and hand it over — Project/Agent/Type
filters that compose with any grouping and window, the whole slice carried
in the URL (us-95.4).

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

**Phase 96 — The type shapes the path** (drafted 2026-08-14, the manager's
request). All four work-item types ride one pipeline today — plan run, plan
review, code run, code review — and the manager called it: the sameness is
ceremony for some types and the wrong shape for others. The state machine
stays shared (decided 2026-08-14); what changes is which gates each type
passes and what words the worker receives. A chore becomes single-shot —
dispatch builds it, the code-review gate stays, retry never re-plans
(us-96.1). A bug's think-first phase becomes a root cause analysis in plain
language — what broke, why, and the proposed fix in words, no diffs — whose
approval unlocks the fix run, reusing the plan machinery whole (us-96.2).
The instruction family grows to match: `chore`, `bug_rca`, `bug_fix` land
with their lifecycles, us-96.3 adds the standalone-story pair (no PRD, no
breakdown — the story is the whole contract) and teaches the Settings
editor to name every member for what it steers. us-96.4 gives the feature
the whole steering wheel: the individual-dispatch refusal extends from the
build phase to planning, plan approvals are worked from the feature page,
and only a child in trouble routes alone — while every child keeps its own
run, branch, and PR. us-96.7 carries that to where work is triaged: on the
Workbench waiting list and the Factory queue a feature with children is
**one** row — batch progress in a line, attention named on the row, the
feature the reorder unit — retiring the US-24.1/24.2 header nesting and
counting one pending item in `org_pending_count`, so the badge and the list
keep agreeing. us-96.5 makes the UI stop dressing all four types in
the same clothes: per-type stage trackers, labels ("Analyzing", not
"Planning", for a bug), and action sets, with the help flowchart brought
along. us-96.6 closes the documented breakdown dead end: a failed breakdown
run leaves its feature at `ready` and dispatchable — the run fails, not the
work item — with stranded features repaired by migration. us-96.10 turns the
same lens on the models: the Anthropic picker catches up to the Claude 5
generation (Opus 5 in, Opus 4.8 out) and each stage is routed to the model
its leverage deserves — Opus 5 at `xhigh` where a bad answer burns gates
(planning, coding), Sonnet 5 for scoped expansion (elaboration), Haiku for
restatement (TLDRs) — applied through the routes and presets the factory
already has, no new plumbing.

The phase carries a **runner-health addendum** (drafted 2026-08-14, from
that day's run-log analysis — three faults the traces named, none of them
the agent's). us-96.8 makes the hand-back speak with one voice: run
`51cd4fd3` spent eight of fifteen minutes deliberating between "leave
changes uncommitted" and `submit_changeset` — both delivered to the same
agent — then had its first submit rejected for containing the
`.factory-out/test_cases.json` the prompt itself ordered written; scratch
becomes filtered-not-fatal, the submit echoes what it received, and the
harness sweeps for modified-but-never-submitted files. us-96.9 makes a
manager stop terminal: run `22b807a5` was stopped at 17:33 and the repair
ladder answered with a wait, a workspace-destroying reclone, and a zombie
CLI boot on a revoked claim — five minutes of probing that closed as
`runner-fault: "no enabled module"` plus two spurious failure rows, for a
run nobody's code broke. us-96.11 extends us-89.1's "files travel; secrets
must not ride them" to telemetry: that same zombie's curl probes landed the
broker's `X-Factory-Local-Key` value verbatim in `run_trace`, twice, on a
table the dashboard renders — redaction at the runner's emit choke point,
a pattern scrub at `record_run_trace`, and a one-off sweep of the ledger.

**Phase 98 — Many branches, one landing** (drafted 2026-08-15, the manager's
request). Work accumulates on branches faster than it lands, and folding
several of them into the default branch has real judgement in it — two agents
touched the same file and only a reader who understands both changes can say
what the merged file should be. That is agent work, and the factory has no
kind for it: there is no merge, rebase or three-way logic anywhere in the api
today, only detection (`MergeConflict`) and a path that hands the conflict
back to an agent. So `merge` becomes a run kind, dispatched on a chore, which
keeps the chore's single-shot shape. us-98.1 adds the kind end to end and
must not be half-done — a kind the database accepts but the runner has never
heard of leaves every such run `queued` forever, which has shipped three
times and is why `test_runner_kind_coverage.py` exists; it also repairs
`run-kinds.ts`, stale by three kinds. us-98.2 gives a chore the branch list
that is the merge's whole subject, validated where it is written rather than
discovered forty minutes into a run, and frozen into `input_context` with
each head sha. us-98.3 makes the claim the authority for reading several refs
at once — the declared branches and the base, nothing else — rather than
requiring the standing `no_claim_checkout` capability a merge should not
need. us-98.4 lands the result on a factory branch behind a pull request, not
straight onto main: conflict resolution is exactly where an agent silently
drops somebody's change, and "the merge succeeded" is not evidence it kept
everything. us-98.5 makes it all-or-nothing — a partial merge looks like
progress and costs more than starting over — and us-98.6 gives the manager a
review that leads with the per-branch account rather than the diff, and
approves with a **merge commit** rather than a squash, for the same reason
release PRs to `prod` are never squashed.

**Phase 99 — The instructions live in the repo** (drafted 2026-08-15, the
manager's request). Worker instructions live in a database and reach agents
as prose inside a context payload, which means the repository — the thing an
agent works in and a human opens — says nothing about how work is done in
it, and the instructions are unversioned against the code they describe and
unreviewable as a diff. They move into the repository: one markdown file per
instruction kind under `.buildmill/`, indexed from `AGENTS.md`. us-99.1 fixes
the map first, in one place, because Phase 96 spent three migrations proving
that a mapping with two homes is a mapping that disagrees with itself —
sixteen kinds get files; `story_breakdown`, `test_case_elaborate` and
`deploy_script_generate` do not, being server-side LLM prompts no agent reads.
us-99.2 gives Build Mill whole ownership of `AGENTS.md` (retiring the fenced
`merge_block` region) and makes `CLAUDE.md` permanently the `@AGENTS.md`
pointer — hand-written `AGENTS.md` content is destroyed on first publish, an
accepted consequence of single ownership, stated rather than discovered.
us-99.3 settles the two-things-called-guidelines collision: `Guidelines.md`
is the project's conventions, `Guidelines_Refresh.md` is the run that
proposes changes to them. us-99.4 turns the invisible pre-dispatch sync into
the manager's own click — the hash column from migration 135 stops gating a
silent commit and starts driving a visible "unpublished" badge — and dispatch
stops writing to GitHub entirely. us-99.5 makes the file authoritative:
`get_work_context` carries a pointer instead of the prose, MCP serves the
content as the fallback for a file that is missing or a project that never
published, and `issues.instruction_set` survives as the per-item contract
layered on top. us-99.6 makes the template set and the file set the same set,
closing a live gap — the template editor's kind list omits **all five** Phase
96 kinds, in two verbatim-duplicated constants, so a new project silently
falls through to factory defaults for exactly the kinds that were added to
give each type its own words. us-99.7 lets a corrected template reach the
projects already using it by **offering**, per instruction, using the
`updated_by` stamp to say which ones the project has never touched and which
would lose a local edit — and never publishing on the superadmin's behalf.

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
