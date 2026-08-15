/** US-2.23: "Where it sits" — a pure projection of existing state onto a
 * stage rail. Nothing here is stored; stages derive from the issue status,
 * the latest run's kind and artifact approvals. US-21.7: a work item's rail
 * ends at Build — where a change is deployed is a question about a release. */

export type StageState =
  | "complete"
  | "in-progress"
  | "waiting"
  | "failed"
  | "not-started"
  | "not-tracked";

export type StageActor = "agent" | "person";

export type Stage = {
  key: string;
  label: string;
  actor: StageActor;
  state: StageState;
};

/** US-12.1: every action the primary slot can offer. `dispatch` posts to
 * /issues/{id}/dispatch; `draft-prd` and `breakdown` were previously
 * reachable only by finding a button buried in a panel further down the
 * page, which is the friction this story removes. */
export type StageAction = (
  | { kind: "link"; label: string; href: string }
  | { kind: "dispatch"; label: string }
  | { kind: "draft-prd"; label: string }
  | { kind: "breakdown"; label: string }
  /** US-20.6: feature-level actions, offered only in feature/epic build mode. */
  | { kind: "batch-dispatch"; label: string }
  | { kind: "approve-all-plans"; label: string }
  /** US-41.2: move every draft story under this feature to `ready` — the
   * manager saying "I have read the breakdown", in one action instead of one
   * edit dialog per story. */
  | { kind: "curate-all"; label: string }
) & {
  /** US-22.10: an action that is shown but declines, with the reason read
   * out loud. A missing button reads as a missing capability; a disabled one
   * carrying its reason teaches the mode. `reasonHref` links to whatever
   * owns the action instead. */
  disabled?: boolean;
  reason?: string;
  reasonHref?: string;
};

/** US-22.10: the one place this copy lives on the client.
 *
 * `dispatch_issue` raises the same sentence (migration 137) so the button and
 * the API say the same thing — if you change one, change the other. */
export function featureOwnsBuildReason(
  featureLabel: string,
  storyCount: number
): string {
  return `${featureLabel} owns the build — dispatch the feature to build all ${storyCount} ${
    storyCount === 1 ? "story" : "stories"
  }`;
}

/** us-96.4/96.5: the sibling refusal for the plan phase, same voice. */
export function featureOwnsPlanReason(
  featureLabel: string,
  storyCount: number
): string {
  return `${featureLabel} owns the plan — dispatch the feature to plan all ${storyCount} ${
    storyCount === 1 ? "story" : "stories"
  }`;
}

/** US-12.1: who the work item is waiting on right now. The primary slot
 * always says something — a blank slot reads as "nothing to do" when the
 * truth is usually "the factory is working" or "you haven't looked yet". */
export type WaitingOn = "you" | "factory" | "nobody";

export type TrackerModel = {
  stages: Stage[];
  context: string;
  action: StageAction | null;
  waitingOn: WaitingOn;
};

export type TrackerInput = {
  issueId: string;
  /** US-49.1: the dispatch preview audits an instruction edit against it. */
  orgId: string;
  type: string;
  status: string;
  latestRunKind: "plan" | "code" | null;
  hasApprovedPlan: boolean;
  hasApprovedPrd: boolean;
  hasPrd: boolean;
  hasChildren: boolean;
  /** US-12.1: a PRD or breakdown run in flight leaves the issue status
   * untouched (dispatch_prd_draft and dispatch_breakdown deliberately do
   * not move it), so without these the slot would keep offering "Draft
   * PRD" while a draft was already running — and a second click would
   * queue a duplicate. */
  activePrdRun?: boolean;
  activeBreakdownRun?: boolean;
  /** US-20.6: `feature`/`epic` extend a feature's rail past Stories into
   * Plan and Build, rolled up from its children. `story` mode is untouched. */
  buildMode?: BuildMode;
  children?: ChildRollup;
  /** US-22.10: the feature that owns this story's build in feature/epic
   * mode. Absent for a story with no parent, which is unaffected in every
   * mode. */
  parent?: ParentFeature | null;
  /** Sequential build mode: another non-terminal issue in this project, if
   * any — dispatch_issue refuses plan/code for every issue but this one
   * while it exists. Mirrors featureOwnsBuild's shape so the primary slot
   * can disable the same way. */
  sequentialBlockedBy?: { id: string; label: string } | null;
  /** us-96.4: whether ANY plan artifact exists (approved, draft or
   * superseded). The line between initial planning — the feature's — and
   * revision, which stays the story's own. */
  hasAnyPlan?: boolean;
};

/** US-22.10: what the story needs to know about the feature above it. */
export type ParentFeature = {
  id: string;
  /** Display id where one exists (`FEAT-1.4`), else the title. */
  label: string;
  /** Non-abandoned children, so the reason can say "all 5 stories". */
  storyCount: number;
};

export type BuildMode = "story" | "feature" | "epic";

/** US-55.5: the preset ids some agent's `run_routes` actually point at.
 *
 * The dispatch override offers only these — "limit to the org, and to agents
 * assigned to the project" (2026-07-30). Rows come straight from
 * `runner_config.run_routes` (`{kind: {preset_id}}`); anything malformed is
 * skipped, because a bad route must degrade to a shorter list, never a
 * broken dispatch bar. */
export function routedPresetIds(rows: { run_routes: unknown }[]): string[] {
  const ids = new Set<string>();
  for (const row of rows) {
    if (!row || typeof row.run_routes !== "object" || row.run_routes === null)
      continue;
    for (const entry of Object.values(
      row.run_routes as Record<string, unknown>
    )) {
      const id = (entry as { preset_id?: unknown } | null)?.preset_id;
      if (typeof id === "string" && id) ids.add(id);
    }
  }
  return [...ids];
}

/** US-20.6: what a feature's children add up to. Counts exclude abandoned
 * stories throughout, mirroring `run_hold_reason`. */
export type ChildRollup = {
  total: number;
  /** Curated out of `draft` — us-15.3 holds every run until this equals total. */
  curated: number;
  planApproved: number;
  inPlanReview: number;
  inCodeReview: number;
  merged: number;
  /** A plan/code run of that kind is queued or running on some child. */
  planRunActive: boolean;
  codeRunActive: boolean;
  /** 1-based position of the story currently in flight, for "story 2 of 9". */
  inFlightPosition: number | null;
  /** The story holding the batch (us-20.5 rule (d)), if any. */
  troubled: { id: string; label: string } | null;
};

/** A feature follows its own rail (Draft → PRD → Stories) unless it was
 * dispatched directly (legal for a childless feature) — then it behaves
 * like any dispatchable item and gets the five-stage rail. */
function usesFeatureRail(input: TrackerInput): boolean {
  return (
    input.type === "feature" &&
    (input.hasChildren ||
      ["draft", "prd-review", "ready"].includes(input.status))
  );
}

function featureRail(input: TrackerInput): TrackerModel {
  const { issueId, status, hasPrd, hasApprovedPrd, hasChildren } = input;

  const draftDone = status !== "draft" || hasPrd;
  const draft: Stage = {
    key: "draft",
    label: "Draft",
    actor: "person",
    state: draftDone ? "complete" : "waiting",
  };

  let prd: Stage;
  if (hasApprovedPrd) {
    prd = { key: "prd", label: "PRD", actor: "agent", state: "complete" };
  } else if (status === "prd-review") {
    prd = { key: "prd", label: "PRD", actor: "agent", state: "waiting" };
  } else {
    prd = { key: "prd", label: "PRD", actor: "agent", state: "not-started" };
  }

  let stories: Stage;
  if (hasChildren) {
    stories = { key: "stories", label: "Stories", actor: "agent", state: "complete" };
  } else if (hasApprovedPrd) {
    stories = { key: "stories", label: "Stories", actor: "agent", state: "waiting" };
  } else {
    stories = { key: "stories", label: "Stories", actor: "agent", state: "not-started" };
  }

  let context = "Draft — describe the feature, then draft a PRD";
  let action: StageAction | null = null;
  let waitingOn: WaitingOn = "you";

  // US-12.1: a run in flight outranks everything — offering "Draft PRD"
  // while a draft is already running invites a duplicate.
  if (input.activePrdRun) {
    context = "PRD — the agent is drafting it";
    waitingOn = "factory";
  } else if (input.activeBreakdownRun) {
    context = "Stories — the agent is splitting the PRD";
    waitingOn = "factory";
  } else if (!draftDone) {
    context = "Draft — describe the feature, then draft a PRD";
    action = { kind: "draft-prd", label: "Draft PRD" };
  } else if (prd.state === "waiting") {
    context = "PRD — the draft awaits your approval";
    // us-12.2: the PRD gate is on the shared review surface, like plan
    // and code review — not a panel on the work item page.
    action = { kind: "link", label: "Review PRD", href: `/review/${issueId}` };
  } else if (prd.state === "not-started") {
    context = "PRD — no draft yet; draft one to define the requirement";
    action = { kind: "draft-prd", label: "Draft PRD" };
  } else if (stories.state === "waiting") {
    context = "Stories — PRD approved; break it into stories";
    action = { kind: "breakdown", label: "Dispatch breakdown" };
  } else if (batched(input)) {
    // US-20.6: in feature/epic mode the feature IS the unit of work, so the
    // rail carries on into Plan and Build instead of dead-ending here.
    return batchedFeatureRail(input, [draft, prd, stories]);
  } else {
    context = "Stories created — the work happens on them";
    waitingOn = "nobody";
  }

  return { stages: [draft, prd, stories], context, action, waitingOn };
}

/** US-20.6: feature-level batching is on and this feature has stories.
 *
 * US-41.1: no longer gated on build mode. The database and the API stopped
 * refusing `story`-mode batches, but this client gate was the reason the
 * button still did not render there — a feature's rail dead-ended at
 * "Stories created — the work happens on them" with no action at all, which
 * is exactly the clicking-one-at-a-time the story set out to remove.
 *
 * The mode now decides only the SHAPE of the code phase, and the confirm
 * says which: one feature-owned run carrying every story, or one run each.
 * `featureOwnsBuild` below stays mode-gated — that is us-22.10's question
 * (may a STORY be built on its own?) and it is a different one. */
function batched(input: TrackerInput): boolean {
  return input.hasChildren && !!input.children && input.children.total > 0;
}

function batchedFeatureRail(
  input: TrackerInput,
  head: Stage[]
): TrackerModel {
  const c = input.children!;
  const n = c.total;
  const story = (k: number) => (k === 1 ? "story" : "stories");

  let planState: StageState = "not-started";
  if (c.planApproved === n) planState = "complete";
  else if (c.planRunActive) planState = "in-progress";
  else if (c.inPlanReview > 0) planState = "waiting";
  else if (c.curated === n) planState = "waiting";
  const plan: Stage = {
    key: "plan",
    label: "Plan",
    actor: "agent",
    state: planState,
  };

  let buildState: StageState = "not-started";
  if (c.merged === n) buildState = "complete";
  else if (c.codeRunActive) buildState = "in-progress";
  else if (c.inCodeReview > 0) buildState = "waiting";
  else if (c.planApproved === n) buildState = "waiting";
  const build: Stage = {
    key: "build",
    label: "Build",
    actor: "agent",
    state: buildState,
  };

  const stages = [...head, plan, build];
  const at = (pos: number | null) =>
    pos ? `story ${pos} of ${n}` : `${n} ${story(n)}`;

  // Trouble outranks everything: a paused batch is the manager's problem,
  // and a progress count that is not moving reads as though it were fine.
  if (c.troubled) {
    return {
      stages,
      context: `Paused — ${c.troubled.label} needs your attention before the batch can go on`,
      action: {
        kind: "link",
        label: `Open ${c.troubled.label}`,
        href: `/issues/${c.troubled.id}`,
      },
      waitingOn: "you",
    };
  }

  if (c.merged === n) {
    return {
      stages,
      context: `Merged — all ${n} ${story(n)} are in`,
      action: null,
      waitingOn: "nobody",
    };
  }

  if (c.curated < n) {
    // US-41.2: this slot used to be empty — a sentence naming an action the
    // page did not provide, leaving the manager to open one edit dialog per
    // story. The gate itself stays (us-15.3: read the breakdown before the
    // factory spends money planning it); only the clicking goes.
    const drafts = n - c.curated;
    return {
      stages,
      context: `Stories — ${drafts} still in draft; curate them before planning`,
      action: {
        kind: "curate-all",
        label: `Curate all ${drafts} ${story(drafts)}`,
      },
      waitingOn: "you",
    };
  }

  if (c.codeRunActive) {
    return {
      stages,
      context: `Build — ${at(c.inFlightPosition)} coding`,
      action: null,
      waitingOn: "factory",
    };
  }
  if (c.planRunActive) {
    return {
      stages,
      context: `Plan — ${at(c.inFlightPosition)} planning`,
      action: null,
      waitingOn: "factory",
    };
  }

  if (c.inCodeReview > 0) {
    return {
      stages,
      context: `Build — ${c.inCodeReview} ${
        c.inCodeReview === 1 ? "diff awaits" : "diffs await"
      } your review`,
      action: { kind: "link", label: "Review the diff", href: `/review` },
      waitingOn: "you",
    };
  }

  if (c.inPlanReview > 0) {
    return {
      stages,
      context: `Plan — ${c.inPlanReview} of ${n} ${
        c.inPlanReview === 1 ? "plan awaits" : "plans await"
      } your review`,
      action: {
        kind: "approve-all-plans",
        label: `Approve all ${c.inPlanReview} plans`,
      },
      waitingOn: "you",
    };
  }

  if (c.planApproved === n) {
    return {
      stages,
      context: `Build — every plan approved; dispatch the code runs`,
      action: { kind: "batch-dispatch", label: `Code all ${n} ${story(n)}` },
      waitingOn: "you",
    };
  }

  return {
    stages,
    context: `Plan — ${n} ${story(n)} ready to plan`,
    action: { kind: "batch-dispatch", label: `Plan all ${n} ${story(n)}` },
    waitingOn: "you",
  };
}

/** US-22.10: the feature that owns this story's code build, or null when the
 * story owns it itself — story mode, or a story with no parent. The same
 * predicate `dispatch_issue` enforces (migration 137), so the button and the
 * API cannot disagree about what is allowed. */
function featureOwnsBuild(input: TrackerInput): ParentFeature | null {
  const batched = input.buildMode === "feature" || input.buildMode === "epic";
  return batched && input.parent ? input.parent : null;
}

/** us-96.4: the feature owns the INITIAL plan of a child that has never
 * been planned — mirrors issue_dispatch_refusal's plan branch (migration
 * 258): draft/ready only, lifted by any existing plan artifact. */
function featureOwnsInitialPlan(input: TrackerInput): ParentFeature | null {
  if (input.hasAnyPlan) return null;
  if (!["draft", "ready"].includes(input.status)) return null;
  const batched = input.buildMode === "feature" || input.buildMode === "epic";
  return batched && input.parent ? input.parent : null;
}

function dispatchableRail(input: TrackerInput): TrackerModel {
  const { issueId, status: s, latestRunKind, hasApprovedPlan } = input;
  const done = s === "done";
  // us-96.5: a bug's think-first phase is a root cause analysis (us-96.2) —
  // same rail, honest words.
  const isBug = input.type === "bug";
  const planLabel = isBug ? "RCA" : "Plan";
  const dispatchPlanLabel = isBug ? "Dispatch RCA" : "Dispatch planning";

  const draftDone = s !== "draft" || latestRunKind !== null;
  const draft: Stage = {
    key: "draft",
    label: "Draft",
    actor: "person",
    state: draftDone ? "complete" : "waiting",
  };

  const planDone =
    hasApprovedPlan ||
    latestRunKind === "code" ||
    ["planned", "needs-fixes", "in-review", "merged", "done"].includes(s);
  let planState: StageState = "not-started";
  if (planDone) planState = "complete";
  else if (s === "planning") planState = "in-progress";
  else if (s === "queued" && !hasApprovedPlan) planState = "in-progress";
  else if (s === "plan-review") planState = "waiting";
  else if (s === "failed" && latestRunKind === "plan") planState = "failed";
  const plan: Stage = { key: "plan", label: planLabel, actor: "agent", state: planState };

  const buildDone = ["merged", "done"].includes(s);
  let buildState: StageState = "not-started";
  if (buildDone) buildState = "complete";
  else if (s === "running" && latestRunKind === "code") buildState = "in-progress";
  else if (s === "queued" && hasApprovedPlan) buildState = "in-progress";
  else if (s === "in-review") buildState = "waiting";
  else if (s === "needs-fixes") buildState = "waiting";
  else if (s === "planned") buildState = "waiting";
  else if (s === "failed" && latestRunKind === "code") buildState = "failed";
  const build: Stage = {
    key: "build",
    label: isBug ? "Fix" : "Build",
    actor: "agent",
    state: buildState,
  };

  // us-96.4/96.5: initial planning belongs to the feature; the button says
  // so instead of erroring (the trouble/revision exemptions never land here
  // — failed/needs-fixes and any existing artifact bypass this branch).
  const planOwner = featureOwnsInitialPlan(input);
  const dispatchPlan: StageAction = planOwner
    ? {
        kind: "dispatch",
        label: dispatchPlanLabel,
        disabled: true,
        reason: featureOwnsPlanReason(planOwner.label, planOwner.storyCount),
        reasonHref: `/issues/${planOwner.id}`,
      }
    : { kind: "dispatch", label: dispatchPlanLabel };

  let context = "";
  let action: StageAction | null = null;
  let waitingOn: WaitingOn = "you";
  if (done) {
    context = "Done";
    waitingOn = "nobody";
  } else if (!draftDone) {
    context = isBug
      ? "Draft — describe the bug, then dispatch the root cause analysis"
      : "Draft — flesh out the story, then dispatch planning";
    action = dispatchPlan;
  } else if (planState === "in-progress") {
    context = isBug
      ? s === "planning"
        ? "RCA — the agent is diagnosing the cause"
        : "RCA — queued for the runner"
      : s === "planning"
        ? "Plan — the agent is writing the implementation and test plans"
        : "Plan — queued for the runner";
    waitingOn = "factory";
  } else if (planState === "waiting") {
    context = isBug
      ? "RCA — the analysis awaits your review"
      : "Plan — implementation and test plans await your review";
    action = {
      kind: "link",
      label: isBug ? "Review RCA" : "Review plan",
      href: `/review/${issueId}`,
    };
  } else if (planState === "failed") {
    context = isBug ? "RCA — the analysis run failed" : "Plan — the plan run failed";
    action = { kind: "dispatch", label: "Re-dispatch" };
  } else if (planState === "not-started") {
    context = isBug
      ? "Ready — dispatch the root cause analysis when you are"
      : "Ready — dispatch planning when you are";
    action = dispatchPlan;
  } else if (buildState === "in-progress") {
    context =
      s === "running"
        ? "Build — the agent's code run is executing"
        : "Build — code run queued for the runner";
    waitingOn = "factory";
  } else if (s === "planned") {
    // US-22.10: in feature/epic mode the feature owns the build. Pressing
    // this would queue a code run that rule (c) immediately holds — a run
    // that exists, sits in the queue, and does nothing. Only the healthy
    // `planned` case defers; `failed` and `needs-fixes` above keep a live
    // button in every mode, because that is the escape hatch us-20.5 left
    // open and greying it would deadlock a stuck batch.
    const owner = featureOwnsBuild(input);
    if (owner) {
      context = "Build — this story is built with its feature";
      action = {
        kind: "dispatch",
        label: "Dispatch code",
        disabled: true,
        reason: featureOwnsBuildReason(owner.label, owner.storyCount),
        reasonHref: `/issues/${owner.id}`,
      };
      waitingOn = "you";
    } else if (isBug) {
      context = "Fix — RCA approved; dispatch the fix run";
      action = { kind: "dispatch", label: "Dispatch fix" };
    } else {
      context = "Build — plan approved; dispatch the code run";
      action = { kind: "dispatch", label: "Dispatch code" };
    }
  } else if (s === "in-review") {
    context = "Build — the diff is ready and waiting on your review";
    action = { kind: "link", label: "Review the diff", href: `/review/${issueId}` };
  } else if (s === "needs-fixes") {
    context = "Build — rejected in review; dispatch a fix run";
    action = { kind: "dispatch", label: "Dispatch fix" };
  } else if (buildState === "failed") {
    context = isBug ? "Fix — the fix run failed" : "Build — the code run failed";
    action = { kind: "dispatch", label: "Re-dispatch" };
  } else if (buildDone) {
    // US-21.7: a work item ends at merged. Where the change is deployed is a
    // question about a RELEASE, which actually knows the answer; the rail no
    // longer pretends to.
    context = "Merged — done";
    waitingOn = "nobody";
  } else {
    context = isBug
      ? "Ready — dispatch the root cause analysis when you are"
      : "Ready — dispatch planning when you are";
    action = dispatchPlan;
  }

  return { stages: [draft, plan, build], context, action, waitingOn };
}

/** us-96.1/96.5: a chore is single-shot — there is no plan stage to draw.
 * Draft → Build, with code review living inside Build's waiting state the
 * way it does on the story rail. planning/plan-review/planned never occur
 * for a chore (migration 255 refuses them). */
function choreRail(input: TrackerInput): TrackerModel {
  const { issueId, status: s } = input;
  const draftDone = s !== "draft" || input.latestRunKind !== null;
  const draft: Stage = {
    key: "draft",
    label: "Draft",
    actor: "person",
    state: draftDone ? "complete" : "waiting",
  };
  const buildDone = ["merged", "done"].includes(s);
  let buildState: StageState = "not-started";
  if (buildDone) buildState = "complete";
  else if (s === "running" || s === "queued") buildState = "in-progress";
  else if (s === "in-review" || s === "needs-fixes") buildState = "waiting";
  else if (s === "failed") buildState = "failed";
  const build: Stage = {
    key: "build",
    label: "Build",
    actor: "agent",
    state: buildState,
  };

  let context = "Ready — dispatch the build when you are";
  let action: StageAction | null = { kind: "dispatch", label: "Dispatch build" };
  let waitingOn: WaitingOn = "you";
  if (buildDone) {
    context = "Merged — done";
    action = null;
    waitingOn = "nobody";
  } else if (s === "queued") {
    context = "Build — queued for the runner";
    action = null;
    waitingOn = "factory";
  } else if (s === "running") {
    context = "Build — the agent is building it";
    action = null;
    waitingOn = "factory";
  } else if (s === "in-review") {
    context = "Build — the diff is ready and waiting on your review";
    action = { kind: "link", label: "Review the diff", href: `/review/${issueId}` };
  } else if (s === "needs-fixes") {
    context = "Build — rejected in review; dispatch a fix run";
    action = { kind: "dispatch", label: "Dispatch fix" };
  } else if (s === "failed") {
    context = "Build — the run failed";
    action = { kind: "dispatch", label: "Re-dispatch" };
  } else if (!draftDone) {
    context = "Draft — describe the chore, then dispatch the build";
  }
  return { stages: [draft, build], context, action, waitingOn };
}

/** Sequential build mode's refusal — worded to match dispatch_issue's own
 * exception (migration 230) so the button and the API agree. Layered on
 * top of whatever rail derived the action: it overrides only a live
 * `dispatch`/`batch-dispatch` action, never a link or an already-disabled
 * one (e.g. "feature owns the build" — that reason is more specific and
 * stays). */
function withSequentialGate(
  model: TrackerModel,
  blockedBy: TrackerInput["sequentialBlockedBy"]
): TrackerModel {
  if (!blockedBy || !model.action || model.action.disabled) return model;
  if (model.action.kind !== "dispatch" && model.action.kind !== "batch-dispatch") {
    return model;
  }
  return {
    ...model,
    action: {
      ...model.action,
      disabled: true,
      reason: `${blockedBy.label} must reach merged first (sequential mode)`,
      reasonHref: `/issues/${blockedBy.id}`,
    },
    waitingOn: "you",
  };
}

export function deriveTracker(input: TrackerInput): TrackerModel {
  const model = usesFeatureRail(input)
    ? featureRail(input)
    : input.type === "chore"
      ? choreRail(input)
      : dispatchableRail(input);
  return withSequentialGate(model, input.sequentialBlockedBy);
}

/** Compact derivation for list/board rows, where only type + status (and
 * sometimes the latest run kind) are cheaply available. Unknowns take the
 * most common reading: a queued run without kind is assumed to be a plan run. */
export function deriveCompact(
  type: string,
  status: string,
  latestRunKind: "plan" | "code" | null = null
): TrackerModel {
  return deriveTracker({
    issueId: "",
    // A list row derives labels only; it never renders the dispatch preview.
    orgId: "",
    type,
    status,
    latestRunKind,
    hasApprovedPlan:
      latestRunKind === "code" ||
      ["planned", "needs-fixes", "in-review", "merged", "done"].includes(status),
    hasApprovedPrd: type === "feature" && status === "ready",
    hasPrd: type === "feature" && status !== "draft",
    hasChildren: false,
  });
}
