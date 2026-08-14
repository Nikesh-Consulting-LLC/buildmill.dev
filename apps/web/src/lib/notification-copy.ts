// US-91.15: what a notification says, and where it goes.
//
// The bell and the API were written for different vocabularies and never
// reconciled. The renderer knew `assigned` / `review_requested` / `blocked`,
// which nothing writes; the API writes `runner_fault` and `deploy_unhealthy`,
// which had no renderer. So every row fell through to the raw type plus the
// literal string "a work item" — false for a runner fault — and `deepLink`
// looked for a `payload.issue_id` these payloads do not carry, so the click
// silently marked the row read and went nowhere.
//
// Pure logic, no React: testable under the bare node runner, which does not
// resolve the `@/` alias.

export type NotificationLike = {
  id: string;
  type: string;
  payload: Record<string, unknown>;
  read_at: string | null;
  created_at: string;
};

/**
 * Every type `db.notify_org_managers` actually writes. Adding a producer
 * without adding a case here is what shipped "runner_fault: a work item" —
 * the fallback below keeps that legible, but this is where it belongs.
 */
export const KNOWN_NOTIFICATION_TYPES = [
  "runner_fault",
  "deploy_unhealthy",
] as const;

function str(payload: Record<string, unknown>, key: string): string | null {
  const v = payload[key];
  return typeof v === "string" && v.trim() ? v.trim() : null;
}

/** Sentence case from a machine enum: `runner_fault` → "Runner fault". */
export function humanizeType(type: string): string {
  const words = type.replace(/[_-]+/g, " ").trim();
  if (!words) return "Notification";
  return words[0].toUpperCase() + words.slice(1);
}

export type NotificationView = {
  /** The bolded subject — an agent's name, a deployment's name, or the
   *  humanised type when the payload names nothing. */
  subject: string;
  /** What happened to it. Never "a work item" unless it is one. */
  summary: string;
  /** The stored `message`, shown beneath. This is the whole point of the
   *  notification and was never displayed. */
  detail: string | null;
};

export function describeNotification(n: NotificationLike): NotificationView {
  const worker = str(n.payload, "worker");
  const deployment = str(n.payload, "deployment");
  const message = str(n.payload, "message");
  const title = str(n.payload, "title");

  switch (n.type) {
    case "runner_fault":
      return {
        subject: worker ?? "An agent",
        summary: "hit a runner fault",
        detail: message,
      };
    case "deploy_unhealthy":
      return {
        subject: deployment ?? "A deployment",
        summary: "failed its health check",
        detail: message,
      };
    default:
      // Degraded, but never machine text and never a claim about an object
      // the notification is not about.
      return {
        subject: humanizeType(n.type),
        summary: title ?? "",
        detail: message,
      };
  }
}

/**
 * Where the row goes. A row that resolves to null must not be a dead button —
 * the bell expands it in place instead, so clicking is never a silent no-op
 * that only marks it read.
 */
export function notificationHref(n: NotificationLike): string | null {
  const runId = str(n.payload, "run_id");
  const issueId = str(n.payload, "issue_id");
  const principalId = str(n.payload, "principal_id");

  if (runId) return `/runs/${runId}`;
  if (issueId)
    return n.type === "review_requested"
      ? `/review/${issueId}`
      : `/issues/${issueId}`;
  if (n.type === "runner_fault" && principalId)
    return `/team/${principalId}/runner`;
  return null;
}

/** Identity for collapsing repeats: the same thing going wrong, again. */
export function repeatKey(n: NotificationLike): string {
  const subject =
    str(n.payload, "worker") ?? str(n.payload, "deployment") ?? "";
  return `${n.type}::${subject}`;
}

export type NotificationGroup<T extends NotificationLike> = {
  /** The newest of the run — what the row renders. */
  head: T;
  /** Every notification in the group, newest first, for the expansion. */
  all: T[];
  unread: number;
};

/**
 * Seven faults from one agent is one row with a count, not seven rows.
 *
 * Grouping is by (type, subject) across the WHOLE loaded window, not just
 * consecutive runs. Consecutive-only was the first cut and it collapsed
 * nothing against real data: the fault sweep notifies once per agent per
 * round, so the feed reads pod-001-5, Architect.001, Build.001, … and then
 * repeats — no two neighbours alike, thirty rows saying five things. The head
 * keeps the newest timestamp and the row shows how far back the run goes, so
 * merging distant events never hides when they happened.
 *
 * Order is preserved by first appearance, which is newest-first.
 */
export function groupNotifications<T extends NotificationLike>(
  items: T[]
): NotificationGroup<T>[] {
  const byKey = new Map<string, NotificationGroup<T>>();
  const out: NotificationGroup<T>[] = [];
  for (const n of items) {
    const key = repeatKey(n);
    const existing = byKey.get(key);
    if (existing) {
      existing.all.push(n);
      if (!n.read_at) existing.unread += 1;
      continue;
    }
    const group: NotificationGroup<T> = {
      head: n,
      all: [n],
      unread: n.read_at ? 0 : 1,
    };
    byKey.set(key, group);
    out.push(group);
  }
  return out;
}
