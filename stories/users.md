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
**Phases 91–96 (42 stories) were closed on 2026-08-15** — built and released to
production, with the manager testing on live rather than through per-story UAT
sittings. The condensed record — including what those phases did *not* prove, the
manager actions each left open (96.10's routing table, 96.11's worker-token
rotation), the Phase 78 known gaps, and the retired-unbuilt-do-not-re-propose list —
is in [APPLICATION.md → Delivery history](../APPLICATION.md#delivery-history).

Thirty-three stories are open. Phases 98, 99 and 100 (requested 2026-08-15) are
built and on production; their stories are `Completed` except the five that record
an unbuilt acceptance criterion and stay `Testing` with the gap named
(us-98.6, us-99.4, us-99.6, us-99.7, us-100.1). Then Phase 97's GitHub linkage
repair (requested 2026-08-15), Phase 101's rebuild of what a release hands the
manager (requested 2026-08-15), Phase 102's rework of the Costs page
(requested 2026-08-15), and the residue carried out of Phases 85–89:

| Order | Story | Title | Status |
|---|---|---|---|
| 1 | [us-97.1](us-97.1-a-moved-repo-relinks-or-asks.md) | A moved repo relinks itself, or asks | New |
| 2 | [us-98.1](us-98.1-the-factory-learns-a-merge-run.md) | The factory learns a merge run | Completed |
| 3 | [us-98.2](us-98.2-a-merge-names-the-branches-it-will-land.md) | A merge names the branches it will land | Completed |
| 4 | [us-98.3](us-98.3-the-agent-reads-every-branch-it-must-merge.md) | The agent reads every branch it must merge | Completed |
| 5 | [us-98.4](us-98.4-a-merge-hands-back-a-branch-and-a-pull-request.md) | A merge hands back a branch and a pull request | Completed |
| 6 | [us-98.5](us-98.5-an-unresolved-branch-fails-the-whole-merge.md) | An unresolved branch fails the whole merge | Completed |
| 7 | [us-98.6](us-98.6-the-manager-reviews-the-merge-summary.md) | The manager reviews the merge summary | Testing |
| 8 | [us-99.1](us-99.1-every-instruction-kind-has-a-file.md) | Every instruction kind has a file, and one map says which | Completed |
| 9 | [us-99.2](us-99.2-agents-md-is-the-index.md) | AGENTS.md is the index, and Build Mill owns it whole | Completed |
| 10 | [us-99.3](us-99.3-project-conventions-become-guidelines-md.md) | Project conventions become Guidelines.md | Completed |
| 11 | [us-99.4](us-99.4-an-unpublished-edit-says-so.md) | An unpublished edit says so, and the manager pushes it | Testing |
| 12 | [us-99.5](us-99.5-the-agent-reads-the-file-and-mcp-fills-the-gap.md) | The agent reads the file, and MCP fills the gap | Completed |
| 13 | [us-99.6](us-99.6-a-template-carries-the-whole-file-set.md) | A template carries the whole file set | Testing |
| 14 | [us-99.7](us-99.7-a-template-edit-offers-itself.md) | A template edit offers itself to the projects using it | Testing |
| 15 | [us-100.1](us-100.1-agent-instructions-is-one-document.md) | Agent Instructions is one document | Testing |
| 16 | [us-100.2](us-100.2-agents-md-is-the-agent-instructions.md) | AGENTS.md is the Agent Instructions | Completed |
| 17 | [us-100.3](us-100.3-the-tabs-say-what-they-hold.md) | The tabs say what they hold | Completed |
| 18 | [us-100.4](us-100.4-a-template-defines-the-agent-instructions.md) | A template defines the Agent Instructions | Completed |
| 19 | [us-100.5](us-100.5-the-refresh-run-proposes-a-document.md) | The refresh run proposes a document, not sections | Completed |
| 20 | [us-100.6](us-100.6-versioning-is-agent-work.md) | Versioning is agent work | Completed |
| 21 | [us-101.1](us-101.1-the-release-agent-reads-the-whole-story.md) | The release agent reads the whole story, not just its title | Testing |
| 22 | [us-101.2](us-101.2-a-release-case-knows-its-section-and-its-story.md) | A release case knows its section, its story, and whether it is critical | Testing |
| 23 | [us-101.3](us-101.3-every-check-is-a-step-and-an-expectation.md) | Every check is a step and an expectation | Testing |
| 24 | [us-101.4](us-101.4-the-notes-are-a-declaration.md) | The notes are a declaration, not a wall of markdown | Testing |
| 25 | [us-101.5](us-101.5-the-release-page-is-the-plan-a-tester-follows.md) | The release page is the plan a tester follows | Testing |
| 26 | [us-101.6](us-101.6-the-release-instruction-reaches-the-agent.md) | The instruction the release agent reads is the one the manager edits | Testing |
| 27 | [us-85.3](us-85.3-a-broken-machine-is-not-a-work-fault.md) | A broken machine is not a work fault | New |
| 28 | [us-87.9](us-87.9-every-foreign-key-has-its-index.md) | Every foreign key has its index | New |
| 29 | [us-87.8](us-87.8-logs-age-out.md) | Logs age out, diffs live outside the row | New |
| 30 | [us-87.10](us-87.10-a-page-load-has-a-budget.md) | A page load has a budget | New |
| 31 | [us-89.3](us-89.3-grok-settings-ride-the-managed-scope.md) | The agent's Grok settings ride the managed scope | New |
| 32 | [us-102.1](us-102.1-the-costs-page-opens-on-the-week.md) | The Costs page opens on the week, and stops explaining itself | Testing |
| 33 | [us-102.2](us-102.2-six-numbers-on-top-that-obey-the-filters.md) | Six numbers on top, and the filters govern them | Testing |

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

**Phase 100 — One document, not twenty-two sections** (drafted 2026-08-15,
the manager's request while inspecting project templates). Phase 99 put the
instructions in the repository; this fixes what they *are*. A project's
conventions are twenty-two catalog sections — Tech stack, Commands, Testing
expectations, Versioning & Release and the rest — that the agent receives
concatenated back into one document anyway. The structure buys nothing and
costs a form with twenty-two boxes where a person wants an editor. It becomes
one markdown document called **Agent Instructions** (us-100.1), the Guidelines
section is decommissioned on a schedule rather than left to rot, and that
document **is** `AGENTS.md`'s body — retiring `.buildmill/Guidelines.md`,
which shipped hours earlier in us-99.3 and is superseded here (us-100.2).
us-100.3 fixes the naming collision the manager actually reported: the tab
labelled **Agent Instructions** today holds the *per-task* worker
instructions, so the one place you would go for your project's instructions
shows you a list of run-kind editors instead — both tabs get named for what
they hold, in one vocabulary shared by project settings, both template
editors, and the audit trail. us-100.4 narrows a template to exactly what a
project has: the Agent Instructions document plus the per-task instructions,
dropping `guideline` sections (retired) and `prompt` sections (platform
machinery that does not belong in a project template). And us-100.5 reshapes
the refresh run, which is section-addressed today and would otherwise keep
writing to a table nothing reads — an agent now proposes a revised Agent
Instructions **and** revised per-task instructions, accepted or rejected
whole, because after Phase 99 improving what agents are told means more than
one file.

**Phase 101 — A release explains itself** (drafted 2026-08-15, the manager's
request, against a hand-written UAT page as the quality bar). A release hands
the manager two paragraphs of prose and an unchecked pile of test cases, and
what he wants is the page he writes by hand: facts at the top, numbered
sections in the order they must be worked, and one line per check that reads
*do this → expect that*, tagged with the story that put it there. The gap is
not prompt polish. The release agent's entire input is a version string, the
`{issue_id, title, type, display_id}` snapshot from the cut, the **first line**
of each commit message, and changed paths with `+`/`-` counts — no acceptance
criteria, no plan, and no sight of the per-story test cases the server copies
onto the release seconds after it submits. So us-101.1 gives it the
requirement, the cases it is about to inherit, the modules the cut already
computed and never showed it, and the migrations in the range — and fixes the
defects found in that payload: every commit message arrives as a one-element
**list**, the commit count reports the page rather than the range, and a
`path_prefix` query over a range GitHub already truncated answers as if it
were complete, so "no migrations" and "the migrations fell off the end" are
the same answer. us-101.2 gives a case the section,
position and criticality a running order needs, and lets an agent tag its own
case with the story it tests — `issue_id` has always existed on the row and
`attach_release_test_cases` has never set it, so an agent-authored case is
permanently unattributable. us-101.3 refuses a check that is a title with
nothing behind it: `test_cases` on hand-back is optional and completely
unvalidated today, so a release can ship with fifteen titles and no steps and
every layer above calls it done. us-101.4 makes the notes a declaration the
app renders rather than markdown or agent-authored HTML — not for taste, but
because the UAT deploy fires *after* the notes are stored, so a masthead an
agent writes claiming a deploy result is a fabrication, and because the only
safe frame for agent HTML has an opaque origin and could never carry the
verdict buttons that gate sign-off. us-101.5 assembles the page — and finds
every masthead number already loaded by that page's own queries, making it a
rendering story rather than a data one. us-101.6 closes the loop the others
depend on: the project's **Release** worker instruction reaches nobody today,
because instruction delivery is keyed on a `runs` row and a release prep has
none. Migration 269 *rewrote* that instruction on 2026-08-15 and backfilled it
into every untouched project, and the new text still tells the agent to finish
with `submit_release_run` — a name that now survives only inside prompt
strings — still sends it to three deploy tools that resolve a `runs` row it
does not have, and now also tells it to read the versioning rules the Agent
Instructions carry, which this path does not deliver. That is why us-100.6's
version proposal is unreachable in practice: the one prompt an agent actually
reads still documents the four-argument hand-back and has never heard of
`proposed_version`.

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

**Phase 102 — Costs leads with the numbers** (drafted 2026-08-15, the
manager's request). `/costs` opens on thirty days and on a five-line paragraph
about gateway metering, and it answers "where did the money go" without ever
answering "what did it buy". us-102.1 makes the default window seven days —
the grain the work happens at — and takes the essay off the page, keeping its
two actionable facts in the help topic. us-102.2 puts six KPI cards on top:
hours agents held work, work items landed, bugs landed, lines written, tokens,
dollars. The point is that **the filters govern them** — project, agent, type
and window re-answer all six, which is what turns "$40" into "$40 fixing four
bugs in Alpha". Tokens and cost read the breakdown's own totals rather than a
second query (one source of dollars); the effort figures come from a new
`work-summary` endpoint over `runs`, joined to `issues` the way
`spend_breakdown` already walks run → issue. They deliberately do **not** come
from `agent_effort_daily`, which is keyed `(org, worker, day)` and would
ignore two of the three filters while sitting beside a table that honours
them.

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
