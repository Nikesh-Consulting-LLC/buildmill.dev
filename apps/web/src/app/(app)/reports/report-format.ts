import { toastError, toastSuccess } from "@/components/ui/toast";
import type { ReportRow } from "./report-types";

/**
 * US-16.6/16.9: how a report reads, shared by the two surfaces that show one.
 *
 * The manager's hub and the superadmin console differ in what they list and
 * what they let you do — but a stack trace is a stack trace, and letting the
 * two drift into formatting the same row differently would be a bug nobody
 * would ever file.
 */

export function when(value: string | null) {
  if (!value) return "—";
  return new Date(value).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Where it failed. The API stamps a request path; a browser stamps the page
 *  URL or the boundary that caught it. Any of them beats the component name. */
export function origin(report: ReportRow): string {
  const context = (report.context ?? {}) as Record<string, unknown>;
  for (const key of ["path", "url", "boundary", "component"]) {
    const value = context[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return "—";
}

/** US-79.4: the reporter's own classification — "network" for fetch and
 *  stale-chunk failures — so connectivity noise reads apart from app defects
 *  at a glance instead of only inside the context JSON. */
export function kind(report: ReportRow): string | null {
  const context = (report.context ?? {}) as Record<string, unknown>;
  const value = context["kind"];
  return typeof value === "string" && value.trim() ? value : null;
}

/** US-79.1 (prod BUG-1): the self-monitoring setup's deliberate test ping.
 *  Structural marker (the sender stamps `component: "verification"`), never
 *  message text. It proves the pipe works — it must read as a confirmation,
 *  not sit beside crashes waiting to be promoted into a bug. */
export function isWiringCheck(report: ReportRow): boolean {
  const context = (report.context ?? {}) as Record<string, unknown>;
  return context["component"] === "verification";
}

/** The report as Markdown — what "Copy details" produces, and the block the
 *  fix prompt wraps. One source, so the two can never disagree. */
export function asMarkdown(report: ReportRow): string {
  const lines = [
    `## ${report.title}`,
    "",
    `- **Source:** ${report.source === "automated" ? "automatic crash report" : "user report"}`,
    `- **Status:** ${report.status}`,
    `- **Occurrences:** ${report.occurrence_count}`,
    `- **First seen:** ${when(report.first_seen_at)}`,
    `- **Last seen:** ${when(report.last_seen_at)}`,
  ];
  if (report.fingerprint) lines.push(`- **Fingerprint:** \`${report.fingerprint}\``);
  if (report.reporter_name || report.reporter_email)
    lines.push(
      `- **Reported by:** ${[report.reporter_name, report.reporter_email]
        .filter(Boolean)
        .join(" · ")}`,
    );
  if (report.message) lines.push("", "### Message", "", report.message);
  if (report.stack_trace)
    lines.push("", "### Stack trace", "", "```", report.stack_trace, "```");
  if (report.context && Object.keys(report.context).length)
    lines.push(
      "",
      "### Context",
      "",
      "```json",
      JSON.stringify(report.context, null, 2),
      "```",
    );
  return lines.join("\n");
}

/** Just the error, for pasting into a search or a chat. */
export function asErrorText(report: ReportRow): string {
  return [report.title, report.message, report.stack_trace]
    .filter(Boolean)
    .join("\n\n");
}

/** Copy, and say whether it worked. `navigator.clipboard` rejects on an
 *  insecure origin or when the document is not focused, and a button that
 *  reports success either way is worse than one that admits failure. */
export async function copyText(text: string, what: string) {
  try {
    await navigator.clipboard.writeText(text);
    toastSuccess(`${what} copied`);
  } catch {
    toastError(`Could not copy the ${what.toLowerCase()}`);
  }
}
