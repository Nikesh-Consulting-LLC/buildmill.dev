"use client";

// US-62.9: one page for "is the app fast" — frontend (us-62.7), API/database
// (us-62.8) and LLM (us-62.3) read together. This page captures nothing new;
// it correlates what those three stories already write, so a slow moment
// can be attributed to a layer instead of switching between three unrelated
// reports. No thresholds are invented here — the numbers are reported, not
// judged.

import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";

type LayerSummary = {
  median: number | null;
  p95: number | null;
  samples: number;
  db_time_share_pct?: number;
};

type Summary = {
  days: number;
  frontend: LayerSummary;
  api: LayerSummary;
  database: LayerSummary;
  llm: LayerSummary;
};

type DetailRow = {
  key: string;
  samples: number;
  median: number | null;
  p95: number | null;
  db_time_share_pct: number | null;
};

const WINDOWS = [
  { days: 1, label: "Today" },
  { days: 7, label: "7 days" },
  { days: 30, label: "30 days" },
];

const LAYERS = [
  { key: "frontend", label: "Frontend", unit: "Page load (LCP)", column: "Route" },
  { key: "api", label: "API", unit: "Request duration", column: "Route" },
  { key: "database", label: "Database", unit: "Time in DB per request", column: "Route" },
  { key: "llm", label: "LLM", unit: "Model call latency", column: "Model" },
] as const;

function ms(value: number | null) {
  if (value == null) return "—";
  if (value < 1000) return `${value}ms`;
  return `${(value / 1000).toFixed(1)}s`;
}

export default function AdminPerformancePage() {
  const [days, setDays] = useState(7);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [openLayer, setOpenLayer] = useState<string | null>(null);
  const [detail, setDetail] = useState<DetailRow[] | null>(null);
  const [detailBusy, setDetailBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setSummary(await apiFetch(`/api/v1/admin/performance?days=${days}`));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [days]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    setOpenLayer(null);
    setDetail(null);
  }, [days]);

  async function toggleDetail(layerKey: string) {
    if (openLayer === layerKey) {
      setOpenLayer(null);
      setDetail(null);
      return;
    }
    setOpenLayer(layerKey);
    setDetail(null);
    setDetailBusy(true);
    try {
      const res = await apiFetch(
        `/api/v1/admin/performance/detail?layer=${layerKey}&days=${days}`,
      );
      setDetail(res?.rows ?? []);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setDetailBusy(false);
    }
  }

  return (
    <div className="flex w-full flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Performance</h1>
        <p className="max-w-3xl text-sm text-muted-foreground">
          Frontend, API, database, and LLM latency — read together, so a slow
          moment is diagnosable as one of the four rather than a mystery.
          These are the measured numbers; deciding what counts as &quot;slow&quot; is
          yours to make from them.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
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

      {summary === null ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : (
        <div className="overflow-x-auto rounded-md border">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b bg-muted/40 text-xs">
                <th className="px-3 py-2 font-medium">Layer</th>
                <th className="px-3 py-2 text-right font-medium">Median</th>
                <th className="px-3 py-2 text-right font-medium">p95</th>
                <th className="px-3 py-2 text-right font-medium">DB time share</th>
                <th className="px-3 py-2 text-right font-medium">Samples</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {LAYERS.map((l) => {
                const s = summary[l.key];
                return (
                  <tr key={l.key} className="border-b last:border-0">
                    <td className="px-3 py-1.5">
                      <div className="font-medium">{l.label}</div>
                      <div className="text-xs text-muted-foreground">{l.unit}</div>
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono text-xs">
                      {ms(s.median)}
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono text-xs">{ms(s.p95)}</td>
                    <td className="px-3 py-1.5 text-right font-mono text-xs">
                      {s.db_time_share_pct != null ? `${s.db_time_share_pct}%` : "—"}
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono text-xs">
                      {s.samples}
                    </td>
                    <td className="px-3 py-1.5 text-right">
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={s.samples === 0}
                        onClick={() => void toggleDetail(l.key)}
                      >
                        {openLayer === l.key ? "Hide" : "By " + l.column.toLowerCase()}
                      </Button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {openLayer && (
        <div className="overflow-x-auto rounded-md border">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b bg-muted/40 text-xs">
                <th className="px-3 py-2 font-medium">
                  {LAYERS.find((l) => l.key === openLayer)?.column}
                </th>
                <th className="px-3 py-2 text-right font-medium">Median</th>
                <th className="px-3 py-2 text-right font-medium">p95</th>
                {openLayer === "api" && (
                  <th className="px-3 py-2 text-right font-medium">DB time share</th>
                )}
                <th className="px-3 py-2 text-right font-medium">Samples</th>
              </tr>
            </thead>
            <tbody>
              {detailBusy ? (
                <tr>
                  <td colSpan={5} className="px-3 py-2 text-xs text-muted-foreground">
                    Loading…
                  </td>
                </tr>
              ) : !detail || detail.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-3 py-2 text-xs text-muted-foreground">
                    No data in this window.
                  </td>
                </tr>
              ) : (
                detail.map((d) => (
                  <tr key={d.key} className="border-b last:border-0">
                    <td className="px-3 py-1.5 font-mono text-xs">{d.key}</td>
                    <td className="px-3 py-1.5 text-right font-mono text-xs">
                      {ms(d.median)}
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono text-xs">{ms(d.p95)}</td>
                    {openLayer === "api" && (
                      <td className="px-3 py-1.5 text-right font-mono text-xs">
                        {d.db_time_share_pct != null ? `${d.db_time_share_pct}%` : "—"}
                      </td>
                    )}
                    <td className="px-3 py-1.5 text-right font-mono text-xs">
                      {d.samples}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
