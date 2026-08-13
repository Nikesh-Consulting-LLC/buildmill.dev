"use client";

// US-60.2: the superadmin's own view of what Settings → Spend already
// computes for one org — grouped by org (or drilled into one org's own
// project/agent breakdown), across every customer at once. Read-only:
// no invoice, no charge, no payment — the same `cost_usd` figures the
// org's own Spend page already renders, made visible in aggregate.

import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

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

const DIMENSIONS = [
  { key: "org", label: "By org" },
  { key: "project", label: "By project" },
  { key: "agent", label: "By agent" },
  { key: "provider", label: "By provider" },
  { key: "model", label: "By model" },
];
const WINDOWS = [
  { days: 1, label: "Today" },
  { days: 7, label: "7 days" },
  { days: 30, label: "30 days" },
  { days: 90, label: "90 days" },
];

function money(value: number | null | undefined) {
  if (value == null) return "—";
  return `$${value.toFixed(value < 1 ? 4 : 2)}`;
}

function tokens(value: number) {
  return value.toLocaleString();
}

function cacheShare(row: { tokens_in: number; cache_read_tokens: number }): string {
  if (!row.tokens_in || !row.cache_read_tokens) return "—";
  return `${Math.round((row.cache_read_tokens / row.tokens_in) * 100)}%`;
}

export default function AdminUsagePage() {
  const [groupBy, setGroupBy] = useState("org");
  const [days, setDays] = useState(30);
  const [selectedOrg, setSelectedOrg] = useState<{ id: string; label: string } | null>(
    null,
  );
  const [data, setData] = useState<Breakdown | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const params = new URLSearchParams({ group_by: groupBy, days: String(days) });
      if (selectedOrg) params.set("org_id", selectedOrg.id);
      setData(await apiFetch(`/api/v1/admin/usage?${params.toString()}`));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [groupBy, days, selectedOrg]);

  useEffect(() => {
    void load();
  }, [load]);

  function pickOrg(row: Row) {
    if (!row.key) return;
    setSelectedOrg({ id: row.key, label: row.label });
    setGroupBy("project");
  }

  function backToAllOrgs() {
    setSelectedOrg(null);
    setGroupBy("org");
  }

  return (
    <div className="flex w-full flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Usage</h1>
        <p className="max-w-3xl text-sm text-muted-foreground">
          Every org&apos;s API usage, across every customer at once — the same
          metered figures Settings → Spend shows one org, including Buildmill
          Agent usage (billed to the platform&apos;s own key, so it reads
          distinctly by provider). Visibility only: no invoice, no charge.
        </p>
      </div>

      {selectedOrg && (
        <div className="flex items-center gap-2 text-sm">
          <Button size="sm" variant="ghost" onClick={backToAllOrgs}>
            ← All orgs
          </Button>
          <span className="text-muted-foreground">
            Showing <span className="font-medium text-foreground">{selectedOrg.label}</span>
          </span>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {DIMENSIONS.map((d) => (
          <Button
            key={d.key}
            size="sm"
            variant={groupBy === d.key ? "default" : "outline"}
            disabled={d.key === "org" && !!selectedOrg}
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
        <p className="text-sm text-muted-foreground">Loading usage…</p>
      ) : data.rows.length === 0 ? (
        <div className="rounded-md border border-dashed p-8 text-sm text-muted-foreground">
          <p className="font-medium text-foreground">
            Nothing metered in this window.
          </p>
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
                <th className="px-3 py-2 text-right font-medium">Tokens in</th>
                <th className="px-3 py-2 text-right font-medium">Tokens out</th>
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
                  <td className="px-3 py-1.5">
                    {data.group_by === "org" && r.key ? (
                      <button
                        type="button"
                        className="underline underline-offset-4 hover:no-underline"
                        onClick={() => pickOrg(r)}
                      >
                        {r.label}
                      </button>
                    ) : (
                      r.label
                    )}
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
                  <td className="hidden px-3 py-1.5 text-right font-mono text-xs lg:table-cell">
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
