// US-103.4: is anything actually preparing this release?
//
// The Workbench told the manager 2026.08.16.3 was "being prepared" for two and
// a half hours after its supervisor had ceased to exist. That was not a bug in
// the wording — the card mapped `releases.status` to prose, and the status was
// correctly `running`. What it had no access to was LIVENESS: the Workbench's
// liveness pass reads `runs`, and release prep is not a `runs` row, so the one
// job in the factory that can hang unobserved was the one job with no
// observation.
//
// `release_prep_runs` has no `last_heartbeat_at` column — `heartbeat_release_prep`
// pushes `claim_expires_at` forward instead (`set claim_expires_at = now() +
// interval '2 hours'`). That makes silence exactly recoverable rather than
// merely approximable: the last beat landed at `claim_expires_at - 2h`, so
// "how long since we last heard" is arithmetic, not a guess. The runner beats
// every 20 seconds (HEARTBEAT_SECONDS), so twenty minutes of silence is sixty
// missed beats — the same threshold the run pass uses for an autonomous
// worker, for the same reason.

/** The lease `claim_release_prep` and `heartbeat_release_prep` both set. */
export const PREP_LEASE_MINUTES = 120;

/** Materially longer than the heartbeat cadence — not merely slow. */
export const PREP_SILENT_MINUTES = 20;

export type PrepReading = "unclaimed" | "working" | "silent" | "abandoned";

export type PrepRow = {
  workerName: string;
  workerPrincipalId: string | null;
  claimedAt: string | null;
  claimExpiresAt: string | null;
};

export type PrepLiveness = {
  reading: PrepReading;
  workerName: string;
  workerPrincipalId: string | null;
  /** Minutes since the agent claimed it. */
  heldMinutes: number;
  /** Minutes since its last heartbeat. */
  silentMinutes: number;
};

/**
 * The four readings a release-prep job can have.
 *
 * - `unclaimed` — cut, but no agent has taken it yet (no prep row, or one
 *   still queued). `heldMinutes` counts the wait instead of the work.
 * - `working` — heartbeating; the ordinary case.
 * - `silent` — lease still valid but nothing heard for a while. May have
 *   stopped; not yet provably dead.
 * - `abandoned` — the lease lapsed. Nothing is preparing this release, and
 *   us-103.1's sweep will fail it. This is the 2026-08-16 state, and the one
 *   the card previously rendered as "being prepared".
 */
export function prepLiveness(
  prep: PrepRow | null,
  since: string | null,
  now: number
): PrepLiveness {
  if (!prep || !prep.claimedAt) {
    return {
      reading: "unclaimed",
      workerName: "",
      workerPrincipalId: null,
      heldMinutes: minutesSince(since, now),
      silentMinutes: 0,
    };
  }
  const heldMinutes = minutesSince(prep.claimedAt, now);
  // The lease is re-armed to now + 2h on every beat, so the last beat landed
  // at (expiry - 2h). With no expiry recorded, the claim itself is the last
  // thing we heard.
  const lastHeard = prep.claimExpiresAt
    ? new Date(prep.claimExpiresAt).getTime() - PREP_LEASE_MINUTES * 60_000
    : new Date(prep.claimedAt).getTime();
  const silentMinutes = Math.max(0, Math.floor((now - lastHeard) / 60_000));

  return {
    reading:
      silentMinutes > PREP_LEASE_MINUTES
        ? "abandoned"
        : silentMinutes >= PREP_SILENT_MINUTES
          ? "silent"
          : "working",
    workerName: prep.workerName,
    workerPrincipalId: prep.workerPrincipalId,
    heldMinutes,
    silentMinutes,
  };
}

function minutesSince(iso: string | null, now: number): number {
  if (!iso) return 0;
  return Math.max(0, Math.floor((now - new Date(iso).getTime()) / 60_000));
}

/** How long, for a human, without pretending to a precision we lack. */
export function duration(minutes: number): string {
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (hours < 24) return rest ? `${hours}h ${rest}m` : `${hours}h`;
  const days = Math.floor(hours / 24);
  return `${days}d ${hours % 24}h`;
}
