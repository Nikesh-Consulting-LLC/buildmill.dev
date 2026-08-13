"use client";

// US-62.1: every run, sliced by kind/project/org/agent, with the duration
// spread (avg/min/max/p95) a timeout decision actually needs — not just an
// average. Generalizes the `preset_outcomes` shape (US-33.6) the same way
// `/admin/usage` generalizes `spend_breakdown` (US-60.2): one endpoint, a
// dimension switcher, a window selector.

import { Fragment, useCallback, useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { RUN_KIND_LABELS, type RunKind } from "@/lib/run-kinds";

type Row = {
  key: string | null;
  label: string;
  runs: number;
  succeeded: number;
  failed: number;
  stopped: number;
  cancelled: number;
  success_rate: number | null;
  cost_usd: number | null;
  avg_seconds: number | null;
  min_seconds: number | null;
  max_seconds: number | null;
  p95_seconds: number | null;
  // US-62.2: only populated when group_by is "agent" — how many times an
  // agent actually tried its finished items (run_attempts, not `runs` alone,
  // since a lease requeue mutates the run row in place rather than adding
  // one).
  attempts: number | null;
  attempts_per_run: number | null;
};

type Breakdown = {
  group_by: string;
  days: number;
  rows: Row[];
};

type DetailRun = {
  id: string;
  kind: string;
  status: string;
  created_at: string | null;
  seconds: number | null;
  cost_usd: number | null;
  error: string | null;
  worker_name: string | null;
  issue_title: string | null;
};

const DIMENSIONS = [
  { key: "kind", label: "By task type" },
  { key: "project", label: "By project" },
  { key: "org", label: "By org" },
  { key: "agent", label: "By agent" },
];
const WINDOWS = [
  { days: 1, label: "Today" },
  { days: 7, label: "7 days" },
  { days: 30, label: "30 days" },
  { days: 90, label: "90 days" },
];

function kindLabel(key: string) {
  return RUN_KIND_LABELS[key as RunKind] ?? key;
}

function money(value: number | null | undefined) {
  if (value == null) return "—";
  return `$${value.toFixed(value < 1 ? 4 : 2)}`;
}

function duration(seconds: number | null) {
  if (seconds == null) return "—";
  if (seconds < 90) return `${seconds}s`;
  return `${Math.round(seconds / 60)}m`;
}

function rate(value: number | null) {
  if (value == null) return "—";
  return `${Math.round(value * 100)}%`;
}

export default function AdminRunAnalyticsPage() {
  const [groupBy, setGroupBy] = useState("kind");
  const [days, setDays] = useState(30);
  const [data, setData] = useState<Breakdown | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openKey, setOpenKey] = useState<string | null>(null);
  const [detail, setDetail] = useState<DetailRun[] | null>(null);
  const [detailBusy, setDetailBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const params = new URLSearchParams({ group_by: groupBy, days: String(days) });
      setData(await apiFetch(`/api/v1/admin/run-analytics?${params.toString()}`));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [groupBy, days]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    // A dimension or window change invalidates whatever detail was open —
    // it names a group value from the PREVIOUS dimension.
    setOpenKey(null);
    setDetail(null);
  }, [groupBy, days]);

  async function toggleDetail(row: Row) {
    if (!row.key) return;
    if (openKey === row.key) {
      setOpenKey(null);
      setDetail(null);
      return;
    }
    setOpenKey(row.key);
    setDetail(null);
    setDetailBusy(true);
    try {
      const params = new URLSearchParams({
        group_by: groupBy,
        key: row.key,
        days: String(days),
      });
      const res = await apiFetch(`/api/v1/admin/run-analytics/detail?${params.toString()}`);
      setDetail(res?.runs ?? []);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setDetailBusy(false);
    }
  }

  function rowLabel(row: Row) {
    if (groupBy === "kind" && row.key) return kindLabel(row.key);
    return row.label;
  }

  return (
    <div className="flex w-full flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Task runs</h1>
        <p className="max-w-3xl text-sm text-muted-foreground">
          Every run, sliced by task type, project, org, or agent — count,
          outcome, and the full duration spread (average, minimum, maximum,
          p95). Use the spread, not just the average, to size a turn ceiling
          or a time limit: an average that hides a 40-minute outlier is the
          exact thing a timeout decision cannot use.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {DIMENSIONS.map((d) => (
          <Button
            key={d.key}
            size="sm"
            variant={groupBy === d.key ? "default" : "outline"}
            onClick={() => setGroupBy(d.key)}
          >
            {d.label}
          </Button>
        ))}
        <span className="ml-auto flex gap-2">
          {WINDOWS.map((w) => (
            <Button
              key={w.days}
              size="sm"
              variant={days === w.days ? "default" : "outline"}
              onClick={() => setDays(w.days)}
            >
              {w.label}
            </Button>
          ))}
        </span>
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}

      {data === null ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : data.rows.length === 0 ? (
        <div className="rounded-md border border-dashed p-8 text-sm text-muted-foreground">
          <p className="font-medium text-foreground">No runs in this window.</p>
        </div>
      ) : (
        <div className="overflow-x-auto rounded-md border">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b bg-muted/40 text-xs">
                <th className="px-3 py-2 font-medium">
                  {DIMENSIONS.find((d) => d.key === data.group_by)?.label.replace(
                    "By ",
                    "",
                  )}
                </th>
                <th className="px-3 py-2 text-right font-medium">Runs</th>
                <th className="px-3 py-2 text-right font-medium">Success</th>
                <th className="px-3 py-2 text-right font-medium">Failed</th>
                <th className="hidden px-3 py-2 text-right font-medium lg:table-cell">
                  Stopped
                </th>
                <th className="hidden px-3 py-2 text-right font-medium lg:table-cell">
                  Cancelled
                </th>
                <th className="px-3 py-2 text-right font-medium">Avg</th>
                <th className="hidden px-3 py-2 text-right font-medium xl:table-cell">
                  Min
                </th>
                <th className="px-3 py-2 text-right font-medium">Max</th>
                <th className="px-3 py-2 text-right font-medium">p95</th>
                <th className="px-3 py-2 text-right font-medium">Cost</th>
                {groupBy === "agent" && (
                  <th
                    className="px-3 py-2 text-right font-medium"
                    title="Attempts per finished run, from run_attempts — a lease requeue mutates the run row in place rather than adding one, so this is the only real retry count."
                  >
                    Attempts/run
                  </th>
                )}
              </tr>
            </thead>
            <tbody>
              {data.rows.map((r) => (
                <Fragment key={r.key ?? "unattributed"}>
                  <tr
                    className="cursor-pointer border-b last:border-0 hover:bg-accent/40"
                    onClick={() => toggleDetail(r)}
                  >
                    <td className="px-3 py-1.5">{rowLabel(r)}</td>
                    <td className="px-3 py-1.5 text-right font-mono text-xs">{r.runs}</td>
                    <td className="px-3 py-1.5 text-right font-mono text-xs">
                      {r.succeeded}{" "}
                      <span className="text-muted-foreground">
                        ({rate(r.success_rate)})
                      </span>
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono text-xs">
                      {r.failed > 0 ? (
                        <span className="text-destructive">{r.failed}</span>
                      ) : (
                        0
                      )}
                    </td>
                    <td className="hidden px-3 py-1.5 text-right font-mono text-xs lg:table-cell">
                      {r.stopped}
                    </td>
                    <td className="hidden px-3 py-1.5 text-right font-mono text-xs lg:table-cell">
                      {r.cancelled}
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono text-xs">
                      {duration(r.avg_seconds)}
                    </td>
                    <td className="hidden px-3 py-1.5 text-right font-mono text-xs xl:table-cell">
                      {duration(r.min_seconds)}
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono text-xs">
                      {duration(r.max_seconds)}
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono text-xs">
                      {duration(r.p95_seconds)}
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono text-xs">
                      {money(r.cost_usd)}
                    </td>
                    {groupBy === "agent" && (
                      <td className="px-3 py-1.5 text-right font-mono text-xs">
                        {r.attempts_per_run == null ? "—" : r.attempts_per_run}
                      </td>
                    )}
                  </tr>
                  {openKey === r.key && (
                    <tr className="bg-muted/20">
                      <td
                        colSpan={groupBy === "agent" ? 12 : 11}
                        className="px-3 py-2"
                      >
                        {detailBusy ? (
                          <p className="text-xs text-muted-foreground">Loading runs…</p>
                        ) : !detail || detail.length === 0 ? (
                          <p className="text-xs text-muted-foreground">
                            No individual runs found for this window.
                          </p>
                        ) : (
                          <ul className="flex flex-col gap-1">
                            {detail.map((d) => (
                              <li
                                key={d.id}
                                className="flex flex-wrap items-center gap-2 text-xs"
                              >
                                <Link
                                  href={`/runs/${d.id}`}
                                  className="font-mono underline underline-offset-4 hover:no-underline"
                                >
                                  {d.id.slice(0, 8)}
                                </Link>
                                <Badge variant="outline" className="text-[11px]">
                                  {kindLabel(d.kind)}
                                </Badge>
                                <span
                                  className={
                                    d.status === "failed" || d.status === "stopped"
                                      ? "text-destructive"
                                      : "text-muted-foreground"
                                  }
                                >
                                  {d.status}
                                </span>
                                {d.issue_title && (
                                  <span className="min-w-0 truncate text-muted-foreground">
                                    {d.issue_title}
                                  </span>
                                )}
                                {d.worker_name && (
                                  <span className="text-muted-foreground">
                                    · {d.worker_name}
                                  </span>
                                )}
                                <span className="ml-auto shrink-0 font-mono text-muted-foreground">
                                  {duration(d.seconds)}
                                </span>
                              </li>
                            ))}
                          </ul>
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
