"use client";

// Phase 95: the Costs section — cost reporting and only reporting, behind the
// us-95.1 `view_costs` gate (checked by page.tsx; this component never
// re-derives it).
//
// us-95.1 moved the US-33.1/33.3 breakdown here from Settings → Spend (the
// rates form stayed behind — configuration lives in Settings). us-95.2 adds
// the daily curve and the window-over-window comparison; us-95.3 the
// work-shaped dimensions (type / epic / work item); us-95.4 the filters and
// the URL that carries the whole slice.
//
// Every figure is a query over the append-only `llm_usage` rows — no counter
// exists to drift — and tokens in and out stay separate everywhere: they have
// different prices, and collapsing them destroys the only information that
// explains why two runs with the same token count cost differently.

import { useCallback, useEffect, useState } from "react";

import { apiCall } from "@/lib/api";
import { createClient } from "@/lib/supabase/client";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { compactTokens, formatWorkSeconds } from "@/lib/work-seconds";
import {
  COST_DIMENSIONS,
  COST_WINDOWS,
  ITEM_TYPES,
  costsParamsFor,
  type CostsInitial,
} from "./costs-url";

type Row = {
  key: string | null;
  label: string;
  tokens_in: number;
  tokens_out: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  cost_usd: number | null;
  calls: number;
  unparsed_calls: number;
};

type Breakdown = {
  group_by: string;
  days: number;
  rows: Row[];
  totals: Omit<Row, "key" | "label">;
};

type TrendPoint = {
  day: string;
  cost_usd: number;
  calls: number;
  unparsed_calls: number;
};

type Trend = {
  days: number;
  series: TrendPoint[];
  total_cost_usd: number | null;
  previous_cost_usd: number | null;
  previous_calls: number;
  calls: number;
  unparsed_calls: number;
};

/** us-102.2: what the window's money bought, over the same slice. */
type WorkSummary = {
  days: number;
  work_seconds: number;
  runs: number;
  items_landed: number;
  bugs_landed: number;
  lines_added: number;
  lines_removed: number;
};

/** us-95.3 AC3: why a row can't be pinned on a work item — named, not dropped. */
const UNATTRIBUTABLE_HINT =
  "Calls with no run behind them (summaries, scoring, the server-side brain), " +
  "and calls from batch runs, whose ledger does not say which item a given " +
  "call served. Never split or pro-rated — a guessed attribution is worse " +
  "than a named gap.";

/** US-38.1: the share of input served from the provider's prompt cache. */
function cacheShare(row: {
  tokens_in: number;
  cache_read_tokens: number;
}): string {
  if (!row.tokens_in || !row.cache_read_tokens) return "—";
  return `${Math.round((row.cache_read_tokens / row.tokens_in) * 100)}%`;
}

function money(value: number | null | undefined) {
  if (value == null) return "—";
  return `$${value.toFixed(value < 1 ? 4 : 2)}`;
}

function tokens(value: number) {
  return value.toLocaleString();
}

type Option = { id: string; name: string };

// US-9.7: `orgId` is resolved server-side (page.tsx) and this component is
// remounted with `key={orgId}` whenever the active workspace changes, so a
// stale org can never linger in state.
export default function CostsView({
  orgId,
  initial,
}: {
  orgId: string;
  initial: CostsInitial;
}) {
  const [groupBy, setGroupBy] = useState(initial.groupBy);
  const [days, setDays] = useState(initial.days);
  const [projectId, setProjectId] = useState<string | null>(initial.projectId);
  const [workerId, setWorkerId] = useState<string | null>(initial.workerId);
  const [itemType, setItemType] = useState<string | null>(initial.itemType);
  const [data, setData] = useState<Breakdown | null>(null);
  const [trend, setTrend] = useState<Trend | null>(null);
  const [summary, setSummary] = useState<WorkSummary | null>(null);
  const [projects, setProjects] = useState<Option[]>([]);
  const [agents, setAgents] = useState<Option[]>([]);
  // US-52.4: runs billed to a Claude subscription bypass the gateway, so they
  // appear in NO figure here — a separate line, never lumped into "could not
  // be measured", which flags metering problems.
  const [subscriptionRuns, setSubscriptionRuns] = useState(0);
  const [error, setError] = useState<string | null>(null);

  // us-95.4 AC4: the view lives in the URL. Defaults are omitted so the bare
  // /costs stays bare; replaceState keeps back-button history unpolluted.
  useEffect(() => {
    const qs = costsParamsFor({ groupBy, days, projectId, workerId, itemType });
    window.history.replaceState(null, "", qs ? `/costs?${qs}` : "/costs");
  }, [groupBy, days, projectId, workerId, itemType]);

  // Filter options, loaded once per org. Revoked agents stay in the list —
  // the money they spent did not leave with them.
  useEffect(() => {
    let cancelled = false;
    const supabase = createClient();
    void Promise.all([
      supabase.from("projects").select("id, name").eq("org_id", orgId).order("name"),
      supabase.from("workers").select("id, name").eq("org_id", orgId).order("name"),
    ]).then(([p, w]) => {
      if (cancelled) return;
      setProjects((p.data as Option[] | null) ?? []);
      setAgents((w.data as Option[] | null) ?? []);
    });
    return () => {
      cancelled = true;
    };
  }, [orgId]);

  const load = useCallback(async () => {
    const filterParams = new URLSearchParams({ days: String(days) });
    if (projectId) filterParams.set("project_id", projectId);
    if (workerId) filterParams.set("worker_id", workerId);
    if (itemType) filterParams.set("item_type", itemType);

    const supabase = createClient();
    const since = new Date(Date.now() - days * 86_400_000).toISOString();
    const { count: subCount } = await supabase
      .from("runs")
      .select("id", { count: "exact", head: true })
      .eq("org_id", orgId)
      .eq("billing", "subscription")
      .gte("created_at", since);
    setSubscriptionRuns(subCount ?? 0);

    try {
      // us-95.4 AC3 / us-102.2 AC2: one set of controls governs all three —
      // the same filter params ride every request, so the band, the curve and
      // the table can't diverge.
      const [breakdown, trendData, summaryData] = await Promise.all([
        apiCall(
          `/api/v1/llm/orgs/${orgId}/spend?group_by=${groupBy}&${filterParams}`,
        ) as Promise<Breakdown>,
        days > 1
          ? (apiCall(
              `/api/v1/llm/orgs/${orgId}/spend-trend?${filterParams}`,
            ) as Promise<Trend>)
          : Promise.resolve(null),
        apiCall(
          `/api/v1/llm/orgs/${orgId}/work-summary?${filterParams}`,
        ) as Promise<WorkSummary>,
      ]);
      setData(breakdown);
      setTrend(trendData);
      setSummary(summaryData);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [orgId, groupBy, days, projectId, workerId, itemType]);

  useEffect(() => {
    void load();
  }, [load]);

  // us-95.4 AC5: the empty state names the slice, so a filtered nothing does
  // not read as a metering fault.
  const activeFilters: { label: string; clear: () => void }[] = [];
  if (itemType) {
    activeFilters.push({
      label: ITEM_TYPES.find((t) => t.key === itemType)?.label ?? itemType,
      clear: () => setItemType(null),
    });
  }
  if (projectId) {
    activeFilters.push({
      label: projects.find((p) => p.id === projectId)?.name ?? "this project",
      clear: () => setProjectId(null),
    });
  }
  if (workerId) {
    activeFilters.push({
      label: agents.find((a) => a.id === workerId)?.name ?? "this agent",
      clear: () => setWorkerId(null),
    });
  }
  const sliceWords = [
    itemType
      ? (ITEM_TYPES.find((t) => t.key === itemType)?.label ?? itemType).toLowerCase()
      : null,
    projectId
      ? `in ${projects.find((p) => p.id === projectId)?.name ?? "that project"}`
      : null,
    workerId
      ? `by ${agents.find((a) => a.id === workerId)?.name ?? "that agent"}`
      : null,
  ]
    .filter(Boolean)
    .join(" ");

  const selectClass =
    "h-8 rounded-md border bg-background px-2 text-sm text-foreground";

  return (
    <div className="flex w-full flex-col gap-6">
      {/* us-102.1: the heading, and nothing else. The five lines that stood
          here explained gateway metering and rate-at-time-of-call pricing —
          true, read once, and pushing the numbers off a phone screen every
          time after that. They live in the help topic now. */}
      <h2 className="text-lg font-semibold">Costs</h2>

      {/* Window + filters: one set of controls governing band, curve, table. */}
      <div className="flex flex-wrap items-center gap-2">
        {COST_WINDOWS.map((w) => (
          <Button
            key={w.days}
            size="sm"
            variant={days === w.days ? "default" : "outline"}
            onClick={() => setDays(w.days)}
          >
            {w.label}
          </Button>
        ))}
        <span className="ml-auto flex flex-wrap gap-2">
          <select
            aria-label="Filter by project"
            className={selectClass}
            value={projectId ?? ""}
            onChange={(e) => setProjectId(e.target.value || null)}
          >
            <option value="">All projects</option>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
          <select
            aria-label="Filter by agent"
            className={selectClass}
            value={workerId ?? ""}
            onChange={(e) => setWorkerId(e.target.value || null)}
          >
            <option value="">All agents</option>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
          <select
            aria-label="Filter by work item type"
            className={selectClass}
            value={itemType ?? ""}
            onChange={(e) => setItemType(e.target.value || null)}
          >
            <option value="">All types</option>
            {ITEM_TYPES.map((t) => (
              <option key={t.key} value={t.key}>
                {t.label}
              </option>
            ))}
          </select>
        </span>
      </div>

      {activeFilters.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          {activeFilters.map((f) => (
            <button
              key={f.label}
              type="button"
              onClick={f.clear}
              title={`Remove the ${f.label} filter`}
              className="inline-flex items-center gap-1 rounded-full border bg-muted/40 px-2.5 py-0.5 text-xs hover:bg-muted"
            >
              {f.label}
              <span aria-hidden>×</span>
            </button>
          ))}
          <button
            type="button"
            onClick={() => {
              setProjectId(null);
              setWorkerId(null);
              setItemType(null);
            }}
            className="text-xs text-muted-foreground underline underline-offset-4 hover:text-foreground"
          >
            Clear
          </button>
        </div>
      )}

      {error && <p className="text-sm text-destructive">{error}</p>}

      {/* us-102.2: the six numbers, governed by the controls above them. */}
      <KpiBand summary={summary} totals={data?.totals ?? null} days={days} />

      {/* us-95.2: the window as a curve, and against the window before it.
          One day is not a curve, so the Today window hides it. */}
      {days > 1 && trend && (
        <TrendChart trend={trend} subscriptionRuns={subscriptionRuns} />
      )}

      <div className="flex flex-wrap items-center gap-2">
        {COST_DIMENSIONS.map((d) => (
          <Button
            key={d.key}
            size="sm"
            variant={groupBy === d.key ? "default" : "outline"}
            onClick={() => setGroupBy(d.key)}
          >
            {d.label}
          </Button>
        ))}
      </div>

      {data === null ? (
        <p className="text-sm text-muted-foreground">Loading spend…</p>
      ) : data.rows.length === 0 ? (
        <div className="rounded-md border border-dashed p-8 text-sm text-muted-foreground">
          {sliceWords ? (
            <>
              <p className="font-medium text-foreground">
                Nothing metered for {sliceWords} in the last {days === 1 ? "day" : `${days} days`}.
              </p>
              <p className="mt-2 max-w-2xl">
                The filters narrowed this to nothing — the meter itself is
                fine. Clear a filter to widen the slice.
              </p>
            </>
          ) : (
            <>
              <p className="font-medium text-foreground">
                Nothing metered in this window.
              </p>
              <p className="mt-2 max-w-2xl">
                Spend appears here as soon as an agent makes a model call
                through the factory gateway. Runs that predate metering report
                nothing rather than zero — the calls happened, they were
                simply never counted.
              </p>
            </>
          )}
        </div>
      ) : (
        <div className="overflow-x-auto rounded-md border">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b bg-muted/40 text-xs">
                <th className="px-3 py-2 font-medium">
                  {COST_DIMENSIONS.find((d) => d.key === data.group_by)?.label.replace(
                    "By ",
                    "",
                  )}
                </th>
                <th className="px-3 py-2 text-right font-medium">Tokens in</th>
                <th className="px-3 py-2 text-right font-medium">Tokens out</th>
                {/* US-38.1: hidden on narrow screens rather than allowed to
                    overflow the table (us-35.7). */}
                <th className="hidden px-3 py-2 text-right font-medium lg:table-cell">
                  Cached
                </th>
                <th className="px-3 py-2 text-right font-medium">Cost</th>
                <th className="px-3 py-2 text-right font-medium">Calls</th>
              </tr>
            </thead>
            <tbody>
              {data.rows.map((r) => (
                <tr key={r.key ?? "unattributed"} className="border-b last:border-0">
                  <td
                    className={cn("px-3 py-1.5", r.key === null && "text-muted-foreground")}
                    title={r.key === null ? UNATTRIBUTABLE_HINT : undefined}
                  >
                    {r.label}
                    {/* US-33.3: what we could not measure, named rather than
                        quietly dropped from the total. */}
                    {r.unparsed_calls > 0 && (
                      <Badge
                        variant="outline"
                        className="ml-2 text-[11px] text-amber-700 dark:text-amber-400"
                        title="Calls whose provider usage could not be read. They are counted as calls but contribute no tokens or cost."
                      >
                        {r.unparsed_calls} unmeasured
                      </Badge>
                    )}
                  </td>
                  <td className="px-3 py-1.5 text-right font-mono text-xs">
                    {tokens(r.tokens_in)}
                  </td>
                  <td className="px-3 py-1.5 text-right font-mono text-xs">
                    {tokens(r.tokens_out)}
                  </td>
                  <td
                    className="hidden px-3 py-1.5 text-right font-mono text-xs lg:table-cell"
                    title={
                      r.cache_read_tokens
                        ? `${tokens(r.cache_read_tokens)} read from cache, ${tokens(
                            r.cache_write_tokens
                          )} written to it`
                        : "nothing reported a cache hit here"
                    }
                  >
                    {cacheShare(r)}
                  </td>
                  <td className="px-3 py-1.5 text-right font-mono text-xs">
                    {money(r.cost_usd)}
                  </td>
                  <td className="px-3 py-1.5 text-right font-mono text-xs">
                    {r.calls}
                  </td>
                </tr>
              ))}
              <tr className="bg-muted/30 font-medium">
                <td className="px-3 py-2">
                  Total
                  {data.totals.unparsed_calls > 0 && (
                    <span className="ml-2 text-xs font-normal text-amber-700 dark:text-amber-400">
                      · {data.totals.unparsed_calls} call(s) could not be measured
                    </span>
                  )}
                  {subscriptionRuns > 0 && (
                    <span className="ml-2 text-xs font-normal text-muted-foreground">
                      · {subscriptionRuns} run(s) on subscription (off-meter, by
                      design)
                    </span>
                  )}
                </td>
                <td className="px-3 py-2 text-right font-mono text-xs">
                  {tokens(data.totals.tokens_in)}
                </td>
                <td className="px-3 py-2 text-right font-mono text-xs">
                  {tokens(data.totals.tokens_out)}
                </td>
                <td className="hidden px-3 py-2 text-right font-mono text-xs lg:table-cell">
                  {cacheShare(data.totals)}
                </td>
                <td className="px-3 py-2 text-right font-mono text-xs">
                  {money(data.totals.cost_usd)}
                </td>
                <td className="px-3 py-2 text-right font-mono text-xs">
                  {data.totals.calls}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/** us-102.2: the six numbers a manager brings to this page — four about the
 * work, two about the money — under the same window and filters as everything
 * below them. That is the whole point of the band: narrowing to one project
 * re-answers all six, which is what turns "$40" into "$40 fixing four bugs in
 * Alpha".
 *
 * Tokens and cost are READ from the breakdown's own totals rather than fetched
 * again. One source of dollars (us-91.11 AC4): a second query could round
 * differently from the Total row six inches below it, and two figures that
 * disagree on one screen destroy trust in both. */
function KpiBand({
  summary,
  totals,
  days,
}: {
  summary: WorkSummary | null;
  totals: Breakdown["totals"] | null;
  days: number;
}) {
  const window = days === 1 ? "today" : `last ${days} days`;
  const pending = "…";
  const tiles = [
    {
      label: "Hours agents worked",
      value: summary ? formatWorkSeconds(summary.work_seconds) : pending,
      note: summary
        ? `over ${summary.runs} run${summary.runs === 1 ? "" : "s"} · ${window}`
        : window,
      // Honest about what the number is: time the agent HELD the run. Paused
      // spans are not subtracted — migration 252's known gap, and claiming
      // otherwise would overstate attention.
      hint:
        "Time each agent held a run, from the moment it claimed the work to " +
        "the moment the run ended. Paused spans are not subtracted.",
    },
    {
      label: "Work items fixed",
      value: summary ? String(summary.items_landed) : pending,
      note: `merged · ${window}`,
      hint:
        "Distinct work items whose code reached the default branch in this " +
        "window. Four attempts on one story count once; a plan run that " +
        "never merged counts none.",
    },
    {
      label: "Bugs fixed",
      value: summary ? String(summary.bugs_landed) : pending,
      note: `merged · ${window}`,
      hint: "The same count, restricted to work items of type Bug.",
    },
    {
      label: "Lines written",
      value: summary ? compactTokens(summary.lines_added) : pending,
      note: summary
        ? `−${compactTokens(summary.lines_removed)} removed · ${window}`
        : window,
      hint:
        "Lines added across the runs in this window, as parsed from each " +
        "run's diff. Removed lines are counted separately, never netted off.",
    },
    {
      label: "Tokens spent",
      value: totals
        ? compactTokens(totals.tokens_in + totals.tokens_out)
        : pending,
      note: totals
        ? `${compactTokens(totals.tokens_in)} in · ${compactTokens(
            totals.tokens_out,
          )} out`
        : window,
      hint:
        "In and out summed for the headline only — they carry different " +
        "prices, which is why every table below keeps them apart.",
    },
    {
      label: "Total cost",
      value: totals ? money(totals.cost_usd) : pending,
      note: `${totals?.calls ?? 0} call${totals?.calls === 1 ? "" : "s"} · ${window}`,
      hint:
        "Each call priced at the rate in force when it was made, so " +
        "repricing a model never rewrites what past runs cost. Rates live in " +
        "Settings → LLM Providers.",
    },
  ];
  return (
    // us-102.2 AC7: two across at 375px. Six stacked cards would push the
    // curve off the screen, which is the defect this band was meant to fix.
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
      {tiles.map((t) => (
        <div key={t.label} className="rounded-lg border p-3" title={t.hint}>
          <p className="text-xs font-medium text-muted-foreground">{t.label}</p>
          <p className="mt-0.5 text-2xl font-semibold tabular-nums">{t.value}</p>
          <p className="text-[11px] text-muted-foreground">{t.note}</p>
        </div>
      ))}
    </div>
  );
}

/** us-95.2: one bar per UTC day. Bars share the table's dollars (same query
 * predicate server-side), so the curve's sum IS the table's total. */
function TrendChart({
  trend,
  subscriptionRuns,
}: {
  trend: Trend;
  subscriptionRuns: number;
}) {
  const max = Math.max(...trend.series.map((p) => p.cost_usd), 0);
  const first = trend.series[0]?.day;
  const last = trend.series[trend.series.length - 1]?.day;

  const delta =
    trend.previous_cost_usd != null && trend.previous_cost_usd > 0 && trend.total_cost_usd != null
      ? ((trend.total_cost_usd - trend.previous_cost_usd) / trend.previous_cost_usd) * 100
      : null;

  return (
    <div className="grid gap-2 rounded-md border p-4">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-sm">
        <span>
          This window:{" "}
          <span className="font-mono font-medium">{money(trend.total_cost_usd)}</span>
        </span>
        <span className="text-muted-foreground">
          previous {trend.days} days:{" "}
          <span className="font-mono">{money(trend.previous_cost_usd)}</span>
        </span>
        {/* A percentage of zero is not a number (us-95.2 AC3). */}
        {delta != null ? (
          <span
            className={cn(
              "font-medium",
              delta > 0
                ? "text-amber-700 dark:text-amber-400"
                : "text-muted-foreground"
            )}
          >
            {delta > 0 ? "+" : ""}
            {Math.round(delta)}%
          </span>
        ) : (
          <span className="text-xs text-muted-foreground">
            nothing to compare against
          </span>
        )}
      </div>

      {max > 0 ? (
        <div className="overflow-x-auto">
          <div
            className="flex h-24 items-end gap-px"
            role="img"
            aria-label={`Daily spend, ${first} to ${last}`}
          >
            {trend.series.map((p) => (
              <div
                key={p.day}
                title={`${p.day}: ${money(p.cost_usd)} over ${p.calls} call${
                  p.calls === 1 ? "" : "s"
                }${p.unparsed_calls ? ` (${p.unparsed_calls} unmeasured)` : ""}`}
                className="flex h-full min-w-[4px] flex-1 flex-col justify-end"
              >
                <div
                  className={cn(
                    "w-full rounded-t-sm",
                    p.unparsed_calls > 0 ? "bg-amber-500/80" : "bg-primary/80"
                  )}
                  style={{
                    // A metered day is never invisible: 2% floor so a cheap
                    // day still registers against an expensive neighbour.
                    height: p.cost_usd > 0 ? `${Math.max(2, (p.cost_usd / max) * 100)}%` : 0,
                  }}
                />
              </div>
            ))}
          </div>
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">
          Calls in this window carried no measured cost — the days are all
          zero, which is a fact, not missing data.
        </p>
      )}

      <div className="flex flex-wrap justify-between gap-2 text-[10px] text-muted-foreground">
        <span>{first}</span>
        <span>
          peak {money(max || null)} / day
          {trend.unparsed_calls > 0 && (
            <span className="text-amber-700 dark:text-amber-400">
              {" "}
              · {trend.unparsed_calls} call(s) unmeasured — real money off this
              curve
            </span>
          )}
          {subscriptionRuns > 0 && (
            <span>
              {" "}
              · {subscriptionRuns} run(s) on subscription, off-meter by design
            </span>
          )}
        </span>
        <span>{last}</span>
      </div>
    </div>
  );
}
