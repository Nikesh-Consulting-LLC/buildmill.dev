---
id: "CHORE-1.2"
issue_id: "9706f393-3a53-4785-866c-1d996e31131a"
type: "story"
title: "Agent dropdown"
parent: null
epic: 1
order: 2
has_plan: true
has_test_plan: true
merge_commit: null
generated_at: "2026-07-29T01:02:57+00:00"
---

# CHORE-1.2 — Agent dropdown

> Approved story and plan, written by Build Mill. The app owns this file; edits here are overwritten on the next approval.

## Story

Agent options are not clear when dispatching and selecting the dropdown for Agent setting before I dispatch. 

it shows duplicate values. Maybe its different agents, but it wont show details.

Attaching an sample image

## Acceptance criteria

_None recorded._

## Approved implementation plan

# CHORE-1.2 — Agent dropdown

The control in the screenshot is the settings picker that sits beside the
Dispatch button on a work item — the one whose first option is "The agent's own
settings" and whose remaining options read "Run as &lt;preset&gt;" (us-33.5's
manager layer). Two separate defects make it unreadable:

1. **The duplicates are real, and they are cross-org.** That picker reads the
   org's run presets with no org filter and relies on row-level security alone
   to narrow them. RLS admits every org the signed-in person belongs to, and
   every org is seeded with the same four presets (Fast, Balanced, Deep,
   Investigate), so a manager in two orgs sees each name twice — different rows,
   identical labels, nothing on screen to tell them apart. Every other place
   that reads presets (the run-presets settings page, an agent's route table)
   scopes the read to one org; this picker is the outlier.
2. **Choosing the wrong twin silently does nothing.** The dispatch endpoint
   records the manager's choice only when the preset and the run belong to the
   same org, and a mismatch is swallowed with a log line. The manager picks
   "Run as Deep", the run is created, and it runs on the agent's own settings
   with no indication the override was dropped.

On top of that the option text is a bare name: it never says which model,
effort, turn cap or budget the preset implies, nor which one the org's default
already is — so even with the duplicates gone, "Deep" means nothing to someone
who has not read the settings page.

## What changes

- Dispatching a work item, the manager sees each run preset exactly once — the
  set belonging to the org that owns the item being dispatched — no matter how
  many orgs they are a member of.
- Each option says what it actually does, not just its name: the model it names
  (or that it inherits the org model), and the settings that distinguish the
  seeded set from each other — reasoning effort, turn cap, spend ceiling — with
  its one-line description available at the point of choice.
- The option the run would use anyway is identifiable before the manager
  chooses: the "agent's own settings" default names the preset it resolves to
  where one is known, and the org default is marked in the list.
- A choice that the factory could not apply is no longer invisible: if the
  dispatch-time override is not recorded (a preset archived a moment earlier, or
  one that does not belong to the item's org), the manager is told the run went
  out on the agent's own settings rather than being left to assume otherwise.
- Nothing about the default path changes: leaving the picker alone still
  dispatches exactly as it does today, with no manager layer written onto the
  run.

## Surfaces touched

- The dispatch control on the work-item stage tracker (both its card and bar
  variants), where the picker is rendered.
- The client-side read of the org's run presets that feeds that picker.
- The dispatch endpoint's recording of the manager's dispatch-time preset, and
  what it reports back when it cannot record one.
- The copy and affordances of the picker itself (option labels, the default
  option, the detail shown for the current selection).

## Risks

- **Scoping to the wrong org.** The item's own org is the only correct source —
  scoping to the signed-in person's "active" org would still show a preset the
  API will refuse whenever someone dispatches an item in a non-active org. The
  dropdown must agree with the rule the override write already enforces
  (preset org = run org), or the duplicates come back as silent no-ops.
- **The picker disappearing.** It is rendered only when the read returns rows;
  an over-tight filter, or a read that now depends on data the tracker does not
  yet have, would remove the override entirely and quietly return every dispatch
  to the agent default. The failure mode to protect against is an empty list,
  not a wrong list.
- **Turning a soft failure into a hard one.** The override write is deliberately
  best-effort so a database blip cannot block a dispatch. Reporting a dropped
  choice must not become a dispatch that fails: the run should still be created
  and the manager told, not refused.
- **Rich option content in a native control.** Native select options cannot hold
  structured markup; pushing detail into them risks unreadable strings or a
  control that behaves differently from the rest of the app. If the picker is
  replaced with the app's own select component, keyboard and mobile behaviour on
  the dispatch path need checking — that path is used constantly.
- **Coverage this deserves:** the org-scoping read wants a fixture where the
  signed-in person is a member of two orgs that both hold same-named presets —
  the single-org case passes either way and proves nothing. The endpoint change
  wants the mismatch case (a preset from another org, and an archived one)
  asserted as reported-and-not-recorded, with the dispatch still succeeding.

## Dependencies

- No migration and no schema change: preset names are already unique per org,
  the presets carry description, model, settings, version and a default flag,
  and runs already carry the org that scopes the choice. This is a read-scoping
  and presentation fix.
- The org that owns the work item must be reachable where the picker renders; if
  the tracker's input does not carry it today, it has to be supplied from the
  work item rather than inferred from the viewer's membership.
- Nothing must land first — the resolver's precedence (agent → supervisor →
  manager) and the run's settings provenance are already shipped and are not
  being changed.
- Adjacent but deliberately out of scope: the run-presets settings page picks
  the person's first membership rather than their active org, which is the same
  class of bug on a different screen; and us-33.5's unshipped acceptance
  criteria (showing the item's complexity beside the choice, prefilling a
  re-dispatch from the previous run) stay unshipped here. Flag them, do not
  fold them in.
- Bar for hand-back: the behaviour above implemented, automated coverage of the
  kinds named under Risks authored alongside it, and `validate_submission`
  clean. Whether a suite can be executed depends on the environment the code run
  lands in and is not an exit criterion here.

## Approved test plan

# CHORE-1.2 — Test plan

Acceptance-level, walked by hand on the work-item page of a project whose items
can be dispatched. Case 1 needs an account that is an active member of two orgs
(both seeded with the standard preset set) — that is the condition the bug
needs, and a single-org account cannot show it either way.

```json
[
  {
    "title": "The dispatch picker lists each preset once for a manager in two orgs",
    "steps": "1. Sign in as a person who is an active member of two organizations, both holding the seeded run presets (Fast, Balanced, Deep, Investigate).\n2. Open a work item that is ready to dispatch and find the settings dropdown beside the Dispatch button.\n3. Open the dropdown and read every option.",
    "expected_result": "Each preset name appears exactly once. The list is the preset set of the organization that owns this work item; presets belonging to the person's other organization are not offered. The first option is still the 'agent's own settings' default."
  },
  {
    "title": "Each option says what it will do, not just its name",
    "steps": "1. On the same work item, open the settings dropdown beside Dispatch.\n2. Read the options, then select one (for example Deep) without dispatching.",
    "expected_result": "Every option identifies what the preset does — the model it names or that it inherits the org model, and its distinguishing settings such as reasoning effort, turn cap and spend ceiling — and the organization's default preset is marked as the default. After selecting one, its description is visible at the point of choice, so the manager can tell two similar presets apart without leaving the page."
  },
  {
    "title": "A chosen preset is what the run actually ran under",
    "steps": "1. Choose a preset that differs from the agent's default (for example Deep) in the dispatch dropdown.\n2. Press Dispatch.\n3. Open the run that was created and inspect its settings and their sources.",
    "expected_result": "The run records the chosen preset, and the settings that came from it are attributed to the manager layer — not to the agent default or a supervisor escalation."
  },
  {
    "title": "Leaving the picker alone dispatches exactly as before",
    "steps": "1. On a work item ready to dispatch, leave the settings dropdown on 'the agent's own settings'.\n2. Press Dispatch.\n3. Open the run that was created and inspect its settings and their sources.",
    "expected_result": "The item dispatches normally with one action and no extra confirmation. The run carries no manager-chosen override; its settings resolve from the agent's own route or the org default, and the run says so."
  },
  {
    "title": "A choice the factory could not apply is reported, not swallowed",
    "steps": "1. Open a work item ready to dispatch and open the settings dropdown, but do not dispatch yet.\n2. In another tab, archive the preset you are about to choose on Settings → Run presets.\n3. Return to the first tab, choose that now-archived preset, and press Dispatch.\n4. Read the page, then open the run that was created.",
    "expected_result": "The dispatch still succeeds — a run exists and the work item moves on. The manager is told plainly that the chosen setting was not applied and the run went out on the agent's own settings; the run's recorded settings agree with that message rather than claiming the archived preset."
  }
]
```

Not covered here, deliberately: the internal precedence rules between the agent,
supervisor and manager layers, and the shape of the read query — those belong to
the coding agent's own automated coverage, which has the working tree and can
actually run.
