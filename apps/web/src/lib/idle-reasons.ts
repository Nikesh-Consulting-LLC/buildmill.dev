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

// ---------------------------------------------------------------------------
// us-116.4: ONE status, rendered the same on every surface.
//
// The API's `db.agent_status` puts presence in front of the idle reason and
// answers `state`; this is the one label map and tone map for it. The roster,
// the runner page, the machine page's slot card, the superadmin Machines card
// and the wizard's Done step all render `state` through these — so an agent
// cannot read "online" on one page and "Paused" on another.
// ---------------------------------------------------------------------------

/** The API's `AGENT_STATES`, in precedence order (offline first: nothing below
 *  it is actionable while the agent is not there). Mirrored here so the web
 *  tests pin the vocabulary against what the roster actually renders. */
export const AGENT_STATES = [
  "offline",
  "revoked",
  "working",
  "stopped",
  "no-roles",
  "no-model",
  "no-grants",
  "queue-held",
  "ready",
] as const;

export type AgentState = (typeof AGENT_STATES)[number];

export const STATUS_LABELS: Record<string, string> = {
  offline: "Offline",
  revoked: "Token revoked",
  working: "Working",
  stopped: "Stopped",
  "no-roles": "No roles checked",
  "no-model": "No model configured",
  "no-grants": "No project grants",
  "queue-held": "Queue held",
  ready: "Ready",
  unknown: "Unknown",
};

/** Tone per state: green for the two healthy words, muted for the two the
 *  manager chose or cannot act on right now (offline, stopped), amber for
 *  every condition that stops work and can be fixed, red for a dead token. */
export function statusTone(state: string): "green" | "muted" | "amber" | "red" {
  if (state === "working" || state === "ready") return "green";
  if (state === "offline" || state === "stopped") return "muted";
  if (state === "revoked") return "red";
  return "amber";
}

const TONE_TEXT: Record<ReturnType<typeof statusTone>, string> = {
  green: "text-emerald-600 dark:text-emerald-400",
  muted: "text-muted-foreground",
  amber: "text-amber-600 dark:text-amber-400",
  red: "text-red-600 dark:text-red-400",
};

const TONE_BADGE: Record<ReturnType<typeof statusTone>, string> = {
  green: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
  muted: "bg-muted text-muted-foreground",
  amber: "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300",
  red: "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300",
};

export function statusTextClass(state: string): string {
  return TONE_TEXT[statusTone(state)];
}

export function statusBadgeClass(state: string): string {
  return TONE_BADGE[statusTone(state)];
}

/** What the API's `agent_status` answers: `state` is what surfaces render;
 *  `reason` is the idle-reason word (kept for the fix links); `detail` the
 *  sentence under it. */
export type AgentStatus = {
  state: string;
  reason?: string;
  detail?: string | null;
  last_seen_at?: string | null;
};

/** The one combine rule, for a surface that already knows presence from the
 *  `live_runner_sessions` view (realtime-fresh) and holds the API's last
 *  answer for the rest: not live beats everything; otherwise the API's state
 *  as it last answered — if that was Offline the agent has just reconnected
 *  and the next poll (triggered by the same realtime event) says the rest.
 *  Mirrors `db.agent_status`, which asks presence first — a test pins it. */
export function stateFor(live: boolean, status: AgentStatus | null | undefined): string {
  if (!live) return "offline";
  if (!status) return "ready";
  if (status.state) return status.state;
  // An API that predates `state` (deploy skew) still answers `reason`; map
  // the two words that changed, pass the rest through.
  const reason = status.reason ?? "ready";
  return reason === "paused" ? "stopped" : reason === "idle" ? "ready" : reason;
}

/** us-116.4: the superadmin Machines card's one line — "N agents not ready
 *  (2 stopped, 1 offline)" — from the same statuses the slot cards render,
 *  worst state first. Empty string when every agent is ready or working. */
export function notReadySummary(states: string[]): string {
  const notReady = states.filter((s) => s !== "ready" && s !== "working");
  if (notReady.length === 0) return "";
  const counts = new Map<string, number>();
  for (const s of notReady) counts.set(s, (counts.get(s) ?? 0) + 1);
  const parts = [...counts.entries()]
    .sort(
      (a, b) =>
        AGENT_STATES.indexOf(a[0] as AgentState) - AGENT_STATES.indexOf(b[0] as AgentState),
    )
    .map(([s, n]) => `${n} ${STATUS_LABELS[s]?.toLowerCase() ?? s}`);
  return `${notReady.length} ${notReady.length === 1 ? "agent" : "agents"} not ready (${parts.join(", ")})`;
}
