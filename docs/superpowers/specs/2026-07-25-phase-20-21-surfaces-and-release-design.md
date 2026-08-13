# Phase 20 & 21 — Manager surfaces, feature batching, and release as a real thing

**Date:** 2026-07-25
**Status:** Approved — stories written, implementation on `phase-20-21-surfaces-and-release`
**Stories:** [us-20.1](../../../stories/us-20.1-worker-instructions-grouped-sections.md)–[us-20.6](../../../stories/us-20.6-feature-page-drives-the-batch.md), [us-21.1](../../../stories/us-21.1-release-entity-cut-from-main.md)–[us-21.7](../../../stories/us-21.7-retire-old-release-model.md)

## What this is

Two phases from one conversation. Phase 20 is four manager-surface fixes plus the unbuilt half of Build-by-Feature. Phase 21 replaces the release model wholesale.

This document records the **decisions and the reasoning**; the story files carry the acceptance criteria.

## Phase 20

### Worker Instructions (us-20.1)

The tab was written for four run kinds. There are seven — `breakdown` (085), `test` (112) and `deploy` (114) were added by later migrations and never given `KIND_META` entries, so they render with raw slugs, sorted to the top because `KIND_ORDER.indexOf` returns `-1`. The regrouping fixes that as a side effect of its real purpose.

Six sections behind a left nav: **Task processing** (moved off Overview, first, per the manager's explicit instruction — item 2 of the request listed it last, item 1 said first, and first won), Requirements (`prd`, `breakdown`), Planning (`plan`), Coding (`code`), Testing (`test`), Release (`release`, `deploy`).

**Draft text lifts to the shell.** Each card owns its draft today; a nav in front of them means switching sections unmounts the pane and discards unsaved edits — a data-loss path the flat page does not have. This was the one non-obvious hazard in an otherwise cosmetic story.

**An "Other" section catches unmapped kinds**, so the next migration to add a kind cannot repeat the bug being fixed.

*Rejected:* scroll-anchor navigation (leaves the wall of cards it exists to remove); nested `Tabs` (reads as confusion directly under the tab bar).

### Brainstorm removal (us-20.2)

Full removal — button, panel, both `/llm/project-setup-brainstorm/*` endpoints, their `llm.py` implementation, tests, and the APPLICATION.md rows. The Project Summary card's own description sells the brainstorm, as do both copies of the walkthrough copy (`help-content.ts`, `help_content.py`); all three are rewritten. us-7.8 stays in `completed/` with a withdrawal note — it shipped, and deleting the file would erase that.

### Epic picker (us-20.3)

Three fixes: filter to `status = 'open'`, add an inline create, promote the field out of "More options".

**The filter lives in the dialog, not the query.** The Work Items hub groups its outline by epic; filtering at the query would orphan every item under a completed epic.

**An edited item's completed epic stays in the list, marked.** Dropping it makes the select fall back to another value and silently reassigns the item on save — a data change from a field nobody touched.

**Inline create, not a nested dialog.** `EpicDialog` calls `router.refresh()` and returns no id; reusing it would discard a half-filled work item and still not select the new epic.

### Test connection (us-20.4)

A new `POST /servers/test-connection` connects from posted values and persists nothing — no row, no Storage write, no host-key capture. `server_id` is accepted so an edit still enforces the row's trusted host key. Editing with a blank credential uses the existing `/servers/{id}/test`.

### Feature batching (us-20.5, us-20.6)

us-17.2 delivered `feature` mode as routing only: `run_hold_reason` holds runs, but nothing dispatches them as a batch and `dispatch_issue` refuses a feature with children. `featureRail` dead-ends at *"Stories created — the work happens on them"* with no action.

**Decisions taken with the manager:**

| Question | Answer | Consequence |
|---|---|---|
| Serialization | **Strictly serial at every phase**, one story in flight per feature, in `sub_no` order | A 10-story planning phase is 10 sequential runs even with idle agents. Chosen knowingly over parallel planning. |
| Plan gate | Per-story approval, **plus** a batch approve | No feature-level review screen; a send-back stays per-story with its own comment |
| Failure | **Pause the whole feature** | Requires the exemption below |

**Rule (c) holds on the run, not the approval.** Story 2 starts the moment story 1 hands back, so review happens in parallel with the next story's work. Serializing on approval would idle every agent behind the manager's inbox.

**Rule (d) must exempt a troubled story's own run.** Otherwise the fix run for the story that broke the batch is held by its own breakage, and two troubled stories hold each other forever. This is the single most important line in the migration.

`dispatch_feature_batch` **reuses `dispatch_issue` per child** inside a nested exception block. `dispatch_issue` picks plan-vs-code, assembles run context and stamps `prev_issue_status`; a parallel implementation would drift within one phase. The exception block is what stops one non-dispatchable child from aborting the batch.

## Phase 21

### The problem

Three mechanisms share the word "release" and none is one:

- `release_records` (us-2.9) — **per work item**, born at merge.
- `release_versions` (us-7.14) — `V<epic>.<seq>`, tags a branch head.
- Deployments — a separate machine; `attach_deploy_events` back-fills `deployed` events by matching commit SHAs.

Nothing enforces an order. The `qa-signoff` and `promotion` gates write an event and an approval row and **drive no status column** — APPLICATION.md says so outright, and `run_deployment` never consults them. Neither has a reject path.

### The model

A **work item ends at Merged**. A **release** is cut from `main` at any time and is the only thing that knows what shipped.

**Decisions taken with the manager:**

| Question | Answer | Reasoning |
|---|---|---|
| Version scheme | **`YYYY.MM.DD.N`**, overridable | `V<epic>.<seq>` assumed an epic root; a release from `main` spans epics. Needs no judgement, always sorts, shows staleness at a glance. |
| Job shape | **One monolithic release run** | Chosen over a run sequence with the cost named: a failed deploy re-runs the job. Mitigated by resume state on the release row. |
| Test cases | **Inherited from included items + agent regression cases** | The factory already materializes cases from approved test plans; re-deriving them drops coverage a plan promised. |
| UAT failure | **Immutable — reject and re-cut** | A version name means exactly one build, forever. Re-deploying a "fixed" build under the same name is the one thing that makes an audit trail lie. |

### Decisions taken from the code

**Pin the commit at creation.** Between cutting a release and an agent claiming it, `main` moves. Without a pinned SHA the notes describe one build and the deploy ships another.

**`get_release_changes` had to exist before anything else.** The agent's entire repo toolkit is `get_repo_tree` and `read_repo_file` — no diff, no compare, no commit range. Asked for "migrations applied, modules affected", an agent could only infer. Confident, unverifiable prose is worse than no audit trail. This reordered the phase.

**The deploy tools were gated to `deploy` runs.** `_held_deploy_run` accepts one kind; a release run that deploys needs it generalised to "a run that owns a deployment", with every rail kept.

**`agent_deploy_refusal` may forbid the production half entirely.** A `protected` deployment is human-only *always*; a production deployment needs the human-set `agent_dispatch_allowed`. Production deployments are typically both. The rails are not weakened — us-13.13 set them deliberately. Promotion dispatches a promotion run where the rails allow and otherwise records the approval and tells the manager to run it. The gate is identical either way; only the hands differ.

**Promotion ships the pinned artifact.** By promotion time `main` is ahead of what UAT tested. us-1.43's `runs/{id}/promote` already pins by SHA with an archived-artifact fallback — reuse, not new machinery.

**Promotion requires both halves.** A healthy deploy nobody tested is not tested; approved cases against a UAT that is down are not a pass.

**No auto-approve for promotion, ever** — written into the story so it is not helpfully added later.

**Rollback is recorded.** Without it, the release list claims a version is live that isn't.

**`runs.merge_commit_sha` survives the retirement.** It is where included items are resolved from; only `release_records` goes.

### Ordering

us-21.2 before us-21.3 (the tool is the blocker). **us-21.7 last** — retiring the old model first would leave the app with no release path mid-phase.

## Cross-phase

`stage-tracker.ts` is touched by both: us-20.6 extends `featureRail` (3 stages → 5), us-21.7 shortens `dispatchableRail` (5 → 3). Different functions, no conflict, but Phase 20 lands first.

Every migration in both phases is applied to **both** Supabase projects — `Software-Factory` (`wdudmfhhqxrqzoyhuzwx`) and `build-mill-dev` (`nncquokoblcfcqyajzmk`) — with `database.types.ts` regenerated, per CLAUDE.md. Functions rebuilt with `create or replace` start from their **current** definition, never an older migration's body: migrations 095/105/106 record that lesson being learned twice.
