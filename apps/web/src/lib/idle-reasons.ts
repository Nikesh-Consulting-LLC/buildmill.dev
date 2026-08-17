/** US-35.1 → us-116.2: why an agent is not working, in the manager's words.
 *
 * The API's `worker_idle_reason` computes the reason once for every surface
 * (the roster's State column, the runner console, the host's Agents tab); this
 * is the one place the vocabulary is rendered, so a reason cannot read one way
 * on Team and another on the console.
 *
 * us-116.2 added the configuration tier — `no-roles`, `no-model` — ABOVE the
 * queue tier: an agent with no roles, no model and no grants used to render
 * identically to a working one ("Idle", muted grey), and the manager had no
 * reason to look closer.
 */

/** "Idle" alone was the whole problem — presence is not permission. `working`
 *  and plain `idle` need no explanation; the rest are conditions a manager has
 *  to act on, so they read as such. */
export const IDLE_LABELS: Record<string, string> = {
  revoked: "Token revoked",
  paused: "Paused",
  "no-roles": "No roles checked",
  "no-model": "No model configured",
  "no-grants": "No project grants",
  "queue-held": "Queue held",
  idle: "Idle",
  working: "Working",
  unknown: "Unknown",
};

/** The API's precedence, strongest first — mirrored here so the web tests pin
 *  the vocabulary and its order against what the roster actually renders. */
export const IDLE_PRECEDENCE: readonly string[] = [
  "revoked",
  "working",
  "paused",
  "no-roles",
  "no-model",
  "no-grants",
  "queue-held",
  "idle",
];

export function idleTone(reason: string): string {
  if (reason === "working") return "text-emerald-600 dark:text-emerald-400";
  if (reason === "idle") return "text-muted-foreground";
  // Everything else is a condition that stops work happening.
  return "text-amber-600 dark:text-amber-400";
}

/** us-116.2: the reasons that mean a CLI session cannot open — the Start
 *  session button is disabled with the sentence up front, instead of failing
 *  after the click. Runtime refusals (offline, already holding a session, a
 *  gateway fault) stay post-click; those are races, not settings. */
export const SESSION_BLOCKING_REASONS: ReadonlySet<string> = new Set([
  "no-roles",
  "no-model",
]);

/** Where the fix for a reason lives, relative to the agent's own pages. The
 *  runner console and the Start session box both link here. */
export function idleFixHref(
  principalId: string,
  reason: string,
): { href: string; label: string } | null {
  switch (reason) {
    case "no-model":
      // The anchor that already exists on the Model per role block.
      return { href: `/team/${principalId}/settings#model-overrides`, label: "Model per role" };
    case "no-roles":
      return { href: `/team/${principalId}/settings#kinds`, label: "What this agent does" };
    case "no-grants":
      return { href: `/team?principal=${principalId}`, label: "Capability grants" };
    case "revoked":
      return { href: "/team", label: "Mint a new token" };
    case "queue-held":
      return { href: "/issues", label: "The work queue" };
    default:
      return null;
  }
}

/** us-116.2: what a blank Model per role row actually resolves to. "Blank
 *  inherits the org's default preset" was an unbacked promise — the default
 *  preset in every org on prod is "Balanced" with `model: null`, so blank
 *  inherited nothing at all and nothing on the page said so. */
export function inheritLine(
  defaultPreset: { name: string; model: string | null } | null | undefined,
): string {
  if (!defaultPreset) return "Inherits nothing — the org has no default preset";
  const model = (defaultPreset.model ?? "").trim();
  return model
    ? `Inherits ${defaultPreset.name} — ${model}`
    : `Inherits ${defaultPreset.name} — no model set`;
}
