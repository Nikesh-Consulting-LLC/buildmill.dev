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
479 stories); Phases 73–75 followed on 2026-08-10, and Phases 76–78 (22 stories)
were confirmed and collapsed on 2026-08-11. The condensed record, including the
retired-unbuilt-do-not-re-propose list, is in
[APPLICATION.md → Delivery history](../APPLICATION.md#delivery-history). What is
still open:

| Order | Story | Title | Status |
|---|---|---|---|
| 1 | [us-79.7](us-79.7-handlers-survive-every-scope.md) | Error handlers survive every scope they serve | Completed |
| 2 | [us-79.6](us-79.6-a-hang-up-before-accept-is-a-hang-up.md) | A hang-up before accept is a hang-up | Completed |
| 3 | [us-79.3](us-79.3-a-hang-up-mid-request-is-not-a-defect.md) | A caller that hangs up mid-request is not a defect | Completed |
| 4 | [us-79.5](us-79.5-database-unreachable-answers-504.md) | The database not answering is a 504 in words | Completed |
| 5 | [us-79.2](us-79.2-a-failed-merge-names-its-credential.md) | A failed merge names its credential and its cure | Completed |
| 6 | [us-79.4](us-79.4-a-network-failure-reports-as-one.md) | A network failure in the browser reports as one | Completed |
| 7 | [us-79.1](us-79.1-a-wiring-check-is-a-confirmation.md) | A wiring check is a confirmation, not a crash | Completed |
| 8 | [us-79.8](us-79.8-an-agent-that-fails-leaves-a-report.md) | An agent that fails leaves a full report | Testing |
| 9 | [us-80.1](us-80.1-two-test-suites-fast-and-full.md) | Two suites: the one you run, and the one you call for | Testing |
| 10 | [us-81.1](us-81.1-a-project-declares-its-test-suites.md) | A project declares its test suites | Testing |
| 11 | [us-81.2](us-81.2-the-suite-pipeline-runs-a-pinned-builds-tests.md) | The suite pipeline runs a pinned build's tests | Testing |
| 12 | [us-81.3](us-81.3-uat-deployment-triggers-the-suites.md) | UAT deployment triggers the suites | Testing |
| 13 | [us-81.4](us-81.4-suite-results-reach-the-cases-and-the-gate.md) | Suite results reach the cases and the gate | Testing |
| 14 | [us-81.5](us-81.5-the-factory-authors-automated-cases.md) | The factory authors automated cases | Testing |
| 15 | [us-81.6](us-81.6-a-code-run-proves-its-tests-ran.md) | A code run proves its tests ran | Testing |
| 16 | [us-82.1](us-82.1-post-go-live-smoke-on-production.md) | Post-go-live smoke on production | Testing |
| 17 | [us-82.2](us-82.2-prose-cases-become-specs.md) | Prose cases become specs | Testing |
| 18 | [us-82.3](us-82.3-modules-name-what-a-release-touched.md) | Modules name what a release touched | Testing |
| 19 | [us-82.4](us-82.4-adopt-untracked-tests.md) | Adopt untracked tests | Testing |
| 20 | [us-83.1](us-83.1-a-pinned-cli-with-its-doors-closed.md) | A pinned CLI with its config doors closed | Testing |
| 21 | [us-83.2](us-83.2-one-session-engine-two-owners.md) | One session engine, two owners | Testing |
| 22 | [us-83.3](us-83.3-a-session-is-a-lease-not-a-latch.md) | A session is a lease, not a latch | Testing |
| 23 | [us-83.4](us-83.4-deliverable-knobs-and-honest-endings.md) | Deliverable knobs and honest endings | Testing |
| 24 | [us-84.1](us-84.1-the-feature-header-clears-the-unanimous-gate.md) | The feature header clears the unanimous gate | Testing |
| 25 | [us-85.1](us-85.1-prepare-agent-workspace.md) | Prepare Agent Workspace on demand | Completed |
| 26 | [us-85.2](us-85.2-a-batch-dispatch-is-one-ordered-request.md) | A batch dispatch is one ordered request | Testing |
| 27 | [us-85.3](us-85.3-a-broken-machine-is-not-a-work-fault.md) | A broken machine is not a work fault | New |
| 28 | [us-86.1](us-86.1-two-switches-and-a-serial-engine.md) | Routing is two checkboxes; execution is always serial | Testing |
| 29 | [us-86.2](us-86.2-the-factory-shows-the-build-not-its-cargo.md) | In the factory shows the build, not its cargo | Testing |
| 30 | [us-87.1](us-87.1-the-shell-asks-once.md) | The shell asks once, not six times | Testing |
| 31 | [us-87.2](us-87.2-a-badge-is-a-count.md) | A badge is a count, not a dataset | Testing |
| 32 | [us-87.5](us-87.5-a-subscription-names-its-rows.md) | A subscription names its rows | Testing |
| 33 | [us-87.3](us-87.3-a-list-ships-titles-not-bodies.md) | A list ships titles, not bodies | Testing |
| 34 | [us-87.4](us-87.4-long-lists-render-what-is-visible.md) | Long lists render what is on screen | Testing |
| 35 | [us-87.6](us-87.6-the-api-keeps-its-connections.md) | The API keeps its connections | Testing |
| 36 | [us-87.7](us-87.7-a-heartbeat-is-not-a-write.md) | A heartbeat is not a write storm | Testing |
| 37 | [us-87.9](us-87.9-every-foreign-key-has-its-index.md) | Every foreign key has its index | New |
| 38 | [us-87.8](us-87.8-logs-age-out.md) | Logs age out, diffs live outside the row | New |
| 39 | [us-87.10](us-87.10-a-page-load-has-a-budget.md) | A page load has a budget | New |
| 40 | [us-87.11](us-87.11-the-app-says-it-heard-you.md) | The app says it heard you | Testing |
| 41 | [us-87.12](us-87.12-a-live-change-announces-itself.md) | A live change announces itself | Testing |
| 40 | [us-88.1](us-88.1-the-cli-window-looks-like-a-cli.md) | The CLI window looks like a CLI, and its text keeps its shape | Testing |
| 42 | [us-89.1](us-89.1-one-secret-one-home.md) | One secret, one home | Testing |
| 43 | [us-89.2](us-89.2-the-environment-is-defined-once.md) | The environment is defined once | Testing |
| 44 | [us-89.3](us-89.3-grok-settings-ride-the-managed-scope.md) | The agent's Grok settings ride the managed scope | New |
| 45 | [us-90.1](us-90.1-a-failed-release-retries.md) | A failed release retries; a rejected one is final | Testing |

**Phase 90 — Release resilience** (drafted 2026-08-13, from release
2026.08.13.1's death): a release that failed before anything shipped gets
a Retry button that re-runs the failed leg — a fresh notes prep or a fresh
UAT deploy — on the same version and the same pinned commit. Immutability
sharpened, not weakened: a version names exactly one build forever; a
failed ATTEMPT retries, a REJECTED build never does (supersede stays).
Manager-triggered only; every attempt audited and visible.

**Phase 89 — The zero-secret workspace** (drafted 2026-08-13, manager's
direction after the FEAT-2.8 token commit): the worker token stops being
copied — no more token-in-remote-URL, token-in-workspace-config,
token-in-helper-script, write-then-delete dances, or scratch-list
whack-a-mole. One home (the slot env file), everything else brokered by
the supervisor: a git credential helper answers fetch/push, a loopback
MCP broker injects the header, workspace files carry at most a
machine-local key that is worthless off the box. Rotation becomes one
file + one restart. The same zero-secret pattern the LLM gateway already
proved for model keys (US-10.3), applied to the last secret on the box.
us-89.2 adds the manager-facing layer: a per-project Environment section
(entries plain or write-only-secret, optionally agent-scoped) delivered
as process env at CLI spawn and discoverable over MCP ("what access do I
have?" answers names, descriptions, and — for a claimed worker — values),
with scrubber registration and a changeset sweep so no delivered secret
can ride a commit.

**Phase 88 — The agent window reads like a terminal** (drafted 2026-08-13,
manager's observation) fixes the one screen where the manager watches an agent
work. It is a light card of grey paragraphs, and the agent's words arrive
mangled: ACP streams message and thought chunks token by token, each is
stripped and run through the whitespace-collapsing clipper, then rejoined with
a space — so every newline in the agent's markdown is deleted and every
punctuation-only token gains a space on both sides (`a health check command .`).
us-88.1 rejoins the chunks verbatim and dresses the console as the terminal it
is, on the same dark surface as the server terminal, with a gutter glyph per
event kind.

**Phase 87 follow-on — the app says it heard you** (drafted 2026-08-13,
from the manager's report after the release: *"it refreshes so fast, there
is no transition or progress indication"*). Making the app fast removed the
feedback its slowness used to provide. us-87.11 recalibrates the signal that
already exists: `--animate-global-progress` is a 1.1-second sweep starting
off-screen left, so a 150 ms navigation shows nothing at all — it becomes an
indeterminate fill with a minimum visible duration, joined by `loading.tsx`
skeletons on the six heaviest routes (there were zero in the app) and
React `<ViewTransition>` so content arrives instead of popping. us-87.12
gives live updates their own local signal — a row that changed tints and
fades — without touching `refreshSilently()`'s deliberate exemption from the
global bar.

**Phase 87 — The app gets fast** (drafted 2026-08-12, from
[docs/performance-analysis-2026-08-12.md](../docs/performance-analysis-2026-08-12.md))
fixes an app that is laggy at 63 work items and would not survive 100s of
projects. The analysis found the lag is not data volume but fixed cost:
the app shell recomputes the whole Things-to-Do dataset on **every**
navigation to print one badge (and `/dashboard` does it twice), reading
`principals` three times by the same key across six sequential round trips
— us-87.1 and us-87.2. The Work Items hub loads every item in the
workspace including each one's **full markdown body**, unscoped and
unbounded, then filters in the browser — us-87.3, with virtualization in
us-87.4. Underneath, **89.5% of all database execution time** (28,016 s of
31,286 s over six weeks) is Realtime decoding WAL for 27 published tables
and unfiltered subscriptions — us-87.5, the single largest win. The API
opens a fresh Postgres connection at each of 214 call sites with no pool
(us-87.6), and authenticates workers with an `UPDATE` that has run 940,000
times, feeding that same WAL (us-87.7). Nothing has ever been deleted —
`api_request_log` is 106 MB, one `runs.diff` row is 30 MB — and no
`pg_cron` job exists (us-87.8); the advisor returns 117 unindexed foreign
keys, 27 unused indexes and 12 per-row `auth.uid()` re-evaluations
(us-87.9). us-87.10 seeds a 100-project / 5,000-item fixture and puts a
budget on each surface, so this decays visibly next time instead of
silently. Build order above is impact-first, not numeric.

**Phase 86 — Routing, simplified** (drafted 2026-08-12, manager's design)
replaces the build-mode radio + Concurrency checkbox with two switches and
one law. Switch 1 "Follow the build order" (default on) keeps Epic→Feature→
Story ordering; off = queue anything in any order — the switch frees the
order, never the law. Switch 2 "Route the feature as one" (default on)
makes the feature the routing unit — batch plan, one feature-owned code
run/PR, no per-story buttons, and ONE repo docs commit per batch action
instead of one per story. `sequential_only`'s dispatch freeze is deleted
outright; the law that replaces it has no checkbox: a project works one
item at a time, start to merge — nothing else starts while an item is
planned, awaiting approval, built, or unmerged. Everything queued behind
it waits wearing the us-74.5 hourglass with the reason, on every surface;
dispatch itself is never refused.

**Phase 85 — Agent readiness** (drafted 2026-08-12) gives the manager a
"Prepare workspace" action on each project row of an agent's Project access:
a popup starts a background job (`workspace_prep_jobs`, migration 246) on the
agent's runner that creates the per-project working directory, fetches latest
code, writes agent + MCP config, registers the granted Tool servers, then
*verifies* the environment (shell, git, factory MCP, tool handshakes) and
reports the resolved run settings — so the first dispatched task starts on a
known-good workspace instead of discovering a broken one (the US-2.8.1
failure mode). us-85.3 closes the loop on the same incident: a run that fails
on a broken bench is recorded `machine-fault` — proven by re-running
us-85.1's environment checks after the failure, not by grepping the
transcript — which raises a runner incident, flags the slot for the US-68.3
auto-repair ladder even while its process is up, and keeps escalation and
story failure stats honest (US-2.8.1's run was mislabelled work-fault).

**Phase 84 — Dashboard batch ergonomics** (drafted 2026-08-12) puts the
batch action where the work already is: when every story under a feature
sits at the same gate, the feature's header row in Waiting on you offers the
one click that clears it — Curate all / Plan all / Code all / Approve all
plans — generalizing us-25.2's plan-gate-only exception over the batch
mechanisms us-20.6/41.1/41.2 already shipped. Projection only: no new
endpoint, RPC, or migration.

**Phase 83 — Interactive agent stability** (drafted 2026-08-11) hardens the
Phase 78 agent against what a full audit of the real CLI (grok 1.0.0, live
handshake + docs.x.ai) and the runner found: the fleet's CLI self-updates and
ingests Claude/Cursor config from workspace repos by default (83.1 pins and
closes both); the CLI-window path has never worked — its env mint is a stub
and it duplicates the run path's session-open at lower maturity (83.2 makes
one engine); the promised 30-minute idle close has no caller and a crashed
session CLI holds the agent forever (83.3); and escalation's effort setting is
silently dropped while the CLI measurably accepts it, with truncated answers
scoring as success (83.4). Not a story, fixed straight on `main`: the
capability check in `agent_sessions.py` calls a nonexistent RPC
(`has_capability` → `has_org_capability`), 403ing every session open.

**Phases 81 & 82 — the reimagined testing process** (drafted 2026-08-11) turn
the factory's six test types into one mechanism: authoring is LLM work (specs
land in the target repo via reviewed changesets, pinned with the release
commit), execution is deterministic (a `deploy.py`-style SSH pipeline runs
pytest/Playwright suites against the deployed UAT instance and parses JUnit
XML — no LLM in the path). Phase 81 delivers suite declarations, the pipeline,
the automatic UAT trigger, flag-controlled sign-off gating
(`blocks_signoff`, advisory by default, per-suite waiver), plan-time
automated-case authoring, and worker-side pre-submit test evidence (the fast
suite, per Phase 80's lesson). Phase 82 adds prod smoke after go-live
(alert + one-click rollback handoff, never auto-rollback), prose→spec
conversion via ordinary `code` runs, a module taxonomy that *suggests* manual
regression cases, and one-click adoption of untracked specs. Unit tests stay
out of the case DB — they are code, recorded as evidence, never tracked rows.

**Phase 80 — Tests you will actually run** cut the api suite from ~30 minutes to
~30 seconds by finding what was actually slow: the tests were on the network,
calling a fake Supabase URL for real and waiting on it. Essential (the default)
blocks outbound resolution and holds back only the tests that need a real
Postgres; Full QA (`--full`) runs everything. Nothing was deleted.

**Phase 79 — Production error truth** answers the first real harvest of the
self-monitoring inbox: prod BUG-1…BUG-8, all promoted 2026-08-11. Seven
stories for eight bugs (BUG-2 and BUG-3 are one merge-failure surface; the
core of BUG-8 already shipped in `8c0f4af` — us-79.7 closes its residue).
The sequence runs noise-first: silence the mask (79.7) and the two hang-up
floods (79.6, 79.3) so real crashes are visible, make the database outage
legible (79.5), then the manager-facing diagnoses (79.2, 79.4, 79.1).
us-79.8 closes the phase by widening it beyond API exceptions: the errors
that never become exceptions — an agent dying holding its claim, a lease
expiring in silence — get their own **Agent failures** console beside
System issues, with the full run context needed to debug them.

Phase 78 — Buildmill Interactive Agent closed on 2026-08-11, all eleven stories:
a third agent type holding a persistent ACP session on platform pools, a console
the manager can watch and type into, and a session that needs no work item at all.

**Known gaps in what shipped**, recorded here so they are not rediscovered by
hitting them:

- `platform_run_config.model_routes` is empty, so an interactive agent created by
  the wizard resolves a model from nowhere and now refuses to run until one is set
  on the agent itself. us-78.5's AC3 — the model comes from the platform config —
  is not true yet. Choosing a fleet-wide default is a superadmin decision about
  which model and whose money, which is why it was left rather than picked.
- No Buildmill-owned fork of `xai-org/grok-build` exists. The provisioner installs
  upstream's binary under our own name, which is the v1 fallback the plan named;
  us-78.1's AC1 is unmet, and Apache-2.0 §6 means the name must change before this
  is presented as a product.
- A session's work is **not promotable** through the submit path (us-78.10 AC4):
  the changes sit in the preserved checkout, and handing them back still needs a
  run. And `llm_usage.session_id` has a column but no writer (AC5) — the gateway
  keys usage on `run_id`, so a session's calls land with both ids null.
- The CLI-window button's **glow** (us-78.11 AC2) has never been observed — it
  needs an interactive agent holding a running run at the moment someone looks at
  the Team page.

## Standing QA checklists

Not stories — reusable full-surface test scripts, run on demand:

- [Full App Browser QA](us-Full-App-Browser-QA.md)
- [Full Git Router QA](us-Full-Git-Router-QA.md)
