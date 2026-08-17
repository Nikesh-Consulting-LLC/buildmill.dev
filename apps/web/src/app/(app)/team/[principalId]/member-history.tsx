"use client";

// us-112.2: the History tab — what this member has done, and how well.
//
// Both sections used to sit at the bottom of Details, under About, Machine and
// Project access — so "what has this agent actually been doing" was the last
// thing on the longest tab. They are one question and now they are one tab.
//
// The activity list is a table for the same reason the roster became one
// (us-112.1): five hand-backs rendered as bordered cards, each with a stacked
// title and sub-line, is three lines of chrome per row for two facts.

import { useEffect, useState } from "react";
import Link from "next/link";
import { Loader2 } from "lucide-react";

import { createClient } from "@/lib/supabase/client";
import { apiFetch } from "@/lib/api";
import { formatLastSeen } from "@/lib/format-time";
import { RUN_KIND_LABELS, type RunKind } from "@/lib/run-kinds";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { principalName, type MemberRow, type WorkerRow } from "../team-view";

type ActivityRow = {
  id: string;
  kind: string;
  status: string;
  when: string;
  title: string;
  issueId: string | null;
  what?: string;
  note?: string | null;
};

type PerfRow = {
  key: string | null;
  label: string;
  runs: number;
  success_rate: number | null;
  avg_seconds: number | null;
  p95_seconds: number | null;
};

/** Seconds as the shortest honest unit — under 90s reads better as seconds
 *  than as "1m". Shared by the Avg and p95 columns so they cannot diverge. */
function duration(seconds: number | null): string {
  if (seconds == null) return "—";
  return seconds < 90 ? `${seconds}s` : `${Math.round(seconds / 60)}m`;
}

export function MemberHistory({
  orgId,
  member,
  workers,
}: {
  orgId: string;
  member: MemberRow;
  workers: WorkerRow[];
}) {
  const isAgent = member.principals?.kind === "agent";
  const [activity, setActivity] = useState<ActivityRow[] | null>(null);
  const [perf, setPerf] = useState<PerfRow[] | null>(null);

  // US-62.2: this agent's own read of us-62.1's run_analytics, filtered to its
  // worker id — the fleet leaderboard and this summary come off the same
  // numbers, so a manager tuning one agent never disagrees with the admin view
  // of the whole fleet. Superadmin-only endpoint: a non-admin viewer simply
  // sees nothing here, the same best-effort pattern used elsewhere.
  useEffect(() => {
    const workerId = workers[0]?.id;
    if (!workerId) return;
    let cancelled = false;
    apiFetch(
      `/api/v1/admin/run-analytics?group_by=kind&days=30&worker_id=${workerId}`,
    )
      .then((d) => {
        if (!cancelled) setPerf(d?.rows ?? null);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [workers]);

  useEffect(() => {
    const supabase = createClient();
    let cancelled = false;
    async function load() {
      const ids = workers.map((w) => w.id);
      if (!ids.length) {
        setActivity([]);
        return;
      }
      const { data } = await supabase
        .from("runs")
        .select("id, kind, status, finished_at, issues!runs_issue_org_fk(id, title)")
        .in("worker_id", ids)
        .not("finished_at", "is", null)
        .order("finished_at", { ascending: false })
        .limit(5);
      // US-35.1: voluntary releases too. A released run loses its worker_id,
      // so the runs query above cannot see it — dropping these would make an
      // agent that keeps handing work back look simply inactive.
      const { data: released } = await supabase
        .from("issue_events")
        .select("id, created_at, issue_id, payload, issues(id, title)")
        .eq("org_id", orgId)
        .eq("type", "run-released")
        .in(
          "payload->>worker",
          workers.map((w) => w.name),
        )
        .order("created_at", { ascending: false })
        .limit(5);
      if (cancelled) return;
      setActivity(
        [
          ...(data ?? []).map((r) => {
            const issue = r.issues as unknown as { id: string; title: string } | null;
            return {
              id: r.id as string,
              kind: r.kind as string,
              status: r.status as string,
              when: r.finished_at as string,
              title: issue?.title ?? "work item",
              issueId: issue?.id ?? null,
              what: "Handed back",
              note: null as string | null,
            };
          }),
          ...(released ?? []).map((e) => {
            const issue = e.issues as unknown as { id: string; title: string } | null;
            const payload = e.payload as { kind?: string; note?: string | null };
            return {
              id: `event-${e.id}`,
              kind: payload.kind ?? "code",
              status: "released",
              when: e.created_at as string,
              title: issue?.title ?? "work item",
              issueId: issue?.id ?? null,
              what: "Released back to the pool",
              note: payload.note ?? null,
            };
          }),
        ]
          .sort((a, b) => (a.when < b.when ? 1 : -1))
          .slice(0, 5),
      );
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [member.principal_id, workers, orgId]);

  const backTo = `?from=${encodeURIComponent(`/team/${member.principal_id}`)}&fromLabel=${encodeURIComponent(principalName(member))}`;

  return (
    <div className="grid min-w-0 gap-5">
      {/* US-35.1: hand-backs AND voluntary releases — what this member has
          actually finished. Renamed from "Recently done" (us-112.2): the list
          includes work handed back to the pool, which was never "done". */}
      <section className="grid min-w-0 gap-2">
        <h3 className="text-sm font-semibold">Activity</h3>
        {activity === null ? (
          <Loader2 className="size-4 animate-spin text-muted-foreground" />
        ) : activity.length === 0 ? (
          <p className="text-xs text-muted-foreground">Nothing handed back yet.</p>
        ) : (
          <Table className="text-xs">
            <TableHeader>
              <TableRow>
                <TableHead className="h-8 w-full max-w-0">Work item</TableHead>
                <TableHead className="h-8 whitespace-nowrap">Outcome</TableHead>
                <TableHead className="h-8 whitespace-nowrap">Kind</TableHead>
                <TableHead className="h-8 whitespace-nowrap text-right">When</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {activity.map((r) => (
                <TableRow key={r.id}>
                  <TableCell className="w-full max-w-0 overflow-hidden py-1.5">
                    {r.issueId ? (
                      <Link
                        href={`/issues/${r.issueId}${backTo}`}
                        className="block truncate hover:underline"
                      >
                        {r.title}
                      </Link>
                    ) : (
                      <span className="block truncate">{r.title}</span>
                    )}
                  </TableCell>
                  <TableCell className="whitespace-nowrap py-1.5 text-muted-foreground">
                    {r.what}
                    {/* The note is why it came back — worth the room when
                        there is one, and nothing at all when there is not. */}
                    {r.note ? ` — “${r.note}”` : ""}
                  </TableCell>
                  <TableCell className="whitespace-nowrap py-1.5 uppercase text-muted-foreground">
                    {r.kind}
                  </TableCell>
                  <TableCell className="whitespace-nowrap py-1.5 text-right text-muted-foreground">
                    {formatLastSeen(r.when)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </section>

      {/* US-62.2: success rate and duration by kind, last 30 days. Empty for a
          non-admin viewer (the endpoint is superadmin-gated) or an agent with
          no finished runs in the window; never an error banner. */}
      {isAgent && perf && perf.length > 0 && (
        <section className="grid min-w-0 gap-2">
          <h3 className="text-sm font-semibold">Performance (last 30 days)</h3>
          <Table className="text-xs">
            <TableHeader>
              <TableRow>
                <TableHead className="h-8 w-full max-w-0">Task type</TableHead>
                <TableHead className="h-8 whitespace-nowrap text-right">Runs</TableHead>
                <TableHead className="h-8 whitespace-nowrap text-right">Success</TableHead>
                <TableHead className="h-8 whitespace-nowrap text-right">Avg</TableHead>
                <TableHead className="h-8 whitespace-nowrap text-right">p95</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {perf.map((p) => (
                <TableRow key={p.key ?? "unattributed"}>
                  <TableCell className="w-full max-w-0 overflow-hidden truncate py-1.5">
                    {p.key ? (RUN_KIND_LABELS[p.key as RunKind] ?? p.key) : p.label}
                  </TableCell>
                  <TableCell className="whitespace-nowrap py-1.5 text-right tabular-nums">
                    {p.runs}
                  </TableCell>
                  <TableCell className="whitespace-nowrap py-1.5 text-right tabular-nums">
                    {p.success_rate == null
                      ? "—"
                      : `${Math.round(p.success_rate * 100)}%`}
                  </TableCell>
                  <TableCell className="whitespace-nowrap py-1.5 text-right tabular-nums">
                    {duration(p.avg_seconds)}
                  </TableCell>
                  <TableCell className="whitespace-nowrap py-1.5 text-right tabular-nums">
                    {duration(p.p95_seconds)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </section>
      )}
    </div>
  );
}
