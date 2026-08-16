/**
 * us-107.2: why nothing in the pool can take a run — the words, shared.
 *
 * Pure, and in `lib` rather than beside the component, for two reasons: the
 * node test runner only globs `src/**\/*.test.ts` and cannot parse JSX, and
 * `workbench/data.ts` is a server module that should not be importing a React
 * component just to name a state.
 *
 * The condition itself is not new — `loadFactoryHealth` has computed it since
 * US-35.5. It was simply never rendered anywhere, so a run that could not move
 * looked identical to one that merely had not moved yet.
 */

export type CapabilityGap =
  | "no-agent-online"
  | "no-project-access"
  | "kind-disabled"
  | "unknown";

/** One sentence per gap, so every surface says the same thing about the same
 *  state. Each names the thing to change, because the point of showing this at
 *  all is that it is fixable. */
export function capabilityGapText(gap: CapabilityGap, kind?: string): string {
  switch (gap) {
    case "no-agent-online":
      return "No agent is online";
    case "no-project-access":
      return "No online agent has access to this project";
    case "kind-disabled":
      return kind
        ? `Every online agent with access has '${kind}' unchecked`
        : "Every online agent with access has this run kind unchecked";
    default:
      return "No capable worker in the pool";
  }
}
