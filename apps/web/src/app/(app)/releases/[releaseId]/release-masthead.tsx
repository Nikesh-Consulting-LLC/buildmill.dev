import { cn } from "@/lib/utils";

/** us-101.5: the facts a tester checks before starting.
 *
 * Every number here was ALREADY loaded by the release page — the deployment
 * attempts, the suite runs, the included items, the checks themselves. The
 * masthead is a rendering story, not a data one, which is also why it belongs
 * to the app and not to the agent: half of these do not exist when the notes
 * are written (the UAT deploy is fired BY the hand-back), so an
 * agent-authored "deploy: success, 1m00s" would be a fabrication.
 *
 * A fact that does not exist yet reads as pending. Showing "0 failed" for a
 * suite that has never run is worse than showing nothing, because it looks
 * like an answer.
 */
export type MastheadFact = {
  label: string;
  value: string;
  /** Dimmed: true when this is a placeholder rather than a measurement. */
  pending?: boolean;
  tone?: "ok" | "bad";
};

function duration(fromISO: string | null, toISO: string | null): string | null {
  if (!fromISO || !toISO) return null;
  const ms = new Date(toISO).getTime() - new Date(fromISO).getTime();
  if (!Number.isFinite(ms) || ms < 0) return null;
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m${String(s % 60).padStart(2, "0")}s`;
}

export function buildFacts({
  commitSha,
  migrations,
  includedCount,
  checks,
  criticalChecks,
  deploy,
  suites,
}: {
  commitSha: string;
  migrations: string[];
  includedCount: number;
  checks: number;
  criticalChecks: number;
  deploy: { status: string; created_at: string; finished_at: string | null } | null;
  suites: { tests_total: number | null; tests_passed: number | null; tests_failed: number | null }[];
}): MastheadFact[] {
  const facts: MastheadFact[] = [
    { label: "Deployed", value: commitSha.slice(0, 7) },
  ];

  facts.push(
    migrations.length
      ? {
          label: migrations.length === 1 ? "Migration" : "Migrations",
          value: migrations.join(", "),
        }
      : { label: "Migrations", value: "none", pending: false }
  );

  facts.push({
    label: includedCount === 1 ? "Work item" : "Work items",
    value: String(includedCount),
  });

  facts.push({
    label: "Checks",
    value: criticalChecks
      ? `${checks} · ${criticalChecks} critical`
      : String(checks),
  });

  if (!deploy) {
    facts.push({ label: "UAT deploy", value: "not started", pending: true });
  } else if (deploy.status === "succeeded") {
    const d = duration(deploy.created_at, deploy.finished_at);
    facts.push({
      label: "UAT deploy",
      value: d ? `success, ${d}` : "success",
      tone: "ok",
    });
  } else if (deploy.status === "failed" || deploy.status === "error") {
    facts.push({ label: "UAT deploy", value: "failed", tone: "bad" });
  } else {
    facts.push({ label: "UAT deploy", value: deploy.status, pending: true });
  }

  const ran = suites.filter((s) => (s.tests_total ?? 0) > 0);
  if (!ran.length) {
    facts.push({ label: "Automated", value: "not run", pending: true });
  } else {
    const passed = ran.reduce((n, s) => n + (s.tests_passed ?? 0), 0);
    const failed = ran.reduce((n, s) => n + (s.tests_failed ?? 0), 0);
    facts.push({
      label: "Automated",
      value: `${passed} passed / ${failed} failed`,
      tone: failed > 0 ? "bad" : "ok",
    });
  }

  return facts;
}

export function ReleaseMasthead({ facts }: { facts: MastheadFact[] }) {
  return (
    <dl className="flex flex-wrap gap-x-8 gap-y-3 border-t pt-4 text-sm">
      {facts.map((f) => (
        <div key={f.label} className="flex min-w-0 flex-col gap-0.5">
          <dt className="text-[0.68rem] font-semibold tracking-wider text-muted-foreground uppercase">
            {f.label}
          </dt>
          <dd
            className={cn(
              "font-mono text-xs tabular-nums break-words",
              f.pending && "text-muted-foreground italic",
              f.tone === "bad" && "text-destructive",
              f.tone === "ok" && "text-emerald-600 dark:text-emerald-400"
            )}
          >
            {f.value}
          </dd>
        </div>
      ))}
    </dl>
  );
}
