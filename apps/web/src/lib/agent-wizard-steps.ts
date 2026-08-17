/** us-111.1: the Add agent wizard's three pure decisions — which steps exist,
 *  when a step may be left, and which agent type opens selected.
 *
 *  Extracted from the wizard component so they can be asserted without a DOM.
 *  The reorder they encode is the point of the story: the roles are asked on
 *  Who (what an agent is for shapes everything after it), the type on Where
 *  (it decides which placements exist at all), and the projects last. Left
 *  inline, "step 3 requires a type" would have quietly survived the type
 *  moving to step 2 — which is exactly the bug class this replaces.
 */

export type WizardStep = "who" | "where" | "what" | "billing" | "done";

export type WizardStepDef = { id: WizardStep; label: string };

/** The visible sequence. Billing appears only for Claude, whose money a run
 *  spends is a Claude-only concept today. */
export function wizardSteps(activeModule: string): WizardStepDef[] {
  return [
    { id: "who", label: "Who" },
    { id: "where", label: "Where" },
    // us-111.1: the type and the roles left this step, so its label follows
    // the contents it still has rather than the ones it used to.
    { id: "what", label: "Projects" },
    ...(activeModule === "claude"
      ? [{ id: "billing" as WizardStep, label: "Billing" }]
      : []),
    { id: "done", label: "Done" },
  ];
}

export type WizardFormState = {
  name: string;
  activeModule: string;
  placement: "self" | "machine" | "pool" | null;
  machineId: string;
  poolId: string;
};

/** May the manager leave this step?
 *
 *  Who wants a name — and deliberately not a role: an agent with nothing
 *  checked is a benched agent, which is warned about (US-77.1) and allowed.
 *  Where now carries the type as well as the placement. Projects is
 *  unconditional; every project starts checked (US-55.1) and an
 *  unchecked-everything agent is warned about, not blocked. */
export function stepValid(step: WizardStep, f: WizardFormState): boolean {
  if (step === "who") return f.name.trim().length > 0;
  if (step === "where") {
    if (!f.activeModule) return false;
    return (
      f.placement === "self" ||
      (f.placement === "machine" && f.machineId.length > 0) ||
      (f.placement === "pool" && f.poolId.length > 0)
    );
  }
  return true;
}

export type OfferedModule = { key: string; poolOnly?: boolean };

/** A pool-only type has nowhere to run when no pool has room.
 *
 *  Takes only the flag, not a whole module: the wizard calls this on the
 *  MODULES rows (which carry label, help and more) and the tests call it on
 *  `{ key, poolOnly }` literals, and neither should have to satisfy the
 *  other's shape. */
export function modulePlaceable(
  m: { poolOnly?: boolean },
  hasSelectablePool: boolean,
): boolean {
  return !m.poolOnly || hasSelectablePool;
}

/** The type that ends up selected, given what the manager picked, what the
 *  superadmin's catalog offers, and whether a pool has room.
 *
 *  us-111.1 makes Buildmill Interactive Agent the default, and it is
 *  pool-only — so without this fallback an org with no pool would open the
 *  Where step on a type that has no placement, and a Next button that can
 *  never enable. Falls through to the first type that can actually run. */
export function resolveActiveModule(
  preferredKey: string,
  offered: OfferedModule[],
  hasSelectablePool: boolean,
): string {
  const chosen = offered.find((m) => m.key === preferredKey);
  if (chosen && modulePlaceable(chosen, hasSelectablePool)) return chosen.key;
  return (
    offered.find((m) => modulePlaceable(m, hasSelectablePool))?.key ??
    offered[0]?.key ??
    ""
  );
}
