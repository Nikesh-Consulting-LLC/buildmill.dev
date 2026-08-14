"use client";

// US-33.3: the same figures, where the money question actually gets asked — on
// the project for project spend, on the agent's console for agent spend. A
// manager wondering what an agent costs is looking at the agent.
//
// One component over one endpoint, so a total on a project page and a total on
// the Spend page can never disagree: both are the same query.

import { useEffect, useState } from "react";
import Link from "next/link";

import { apiCall } from "@/lib/api";
import { useCanViewCosts } from "@/lib/use-can-view-costs";

type Totals = {
  tokens_in: number;
  tokens_out: number;
  cost_usd: number | null;
  calls: number;
  unparsed_calls: number;
};

export function SpendSummary({
  orgId,
  projectId,
  workerId,
  days = 30,
  label,
}: {
  orgId: string;
  projectId?: string;
  workerId?: string;
  days?: number;
  label?: string;
}) {
  const [totals, setTotals] = useState<Totals | null>(null);
  const [failed, setFailed] = useState(false);
  // us-95.1 AC6: the breakdown moved behind the Costs gate — the link renders
  // only for viewers who hold the key; everyone else keeps the same figures
  // without a door they'd be turned away from.
  const canViewCosts = useCanViewCosts(orgId);

  useEffect(() => {
    let cancelled = false;
    const params = new URLSearchParams({ group_by: "model", days: String(days) });
    if (projectId) params.set("project_id", projectId);
    if (workerId) params.set("worker_id", workerId);
    apiCall(`/api/v1/llm/orgs/${orgId}/spend?${params}`)
      .then((res) => {
        if (!cancelled) setTotals(res?.totals ?? null);
      })
      .catch(() => {
        // A spend figure that cannot be fetched is not worth a broken page.
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [orgId, projectId, workerId, days]);

  if (failed || !totals) return null;
  if (totals.calls === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        {label ?? "Spend"}: nothing metered in the last {days} days.
      </p>
    );
  }
  return (
    <p className="text-xs text-muted-foreground">
      {label ?? "Spend"} ({days}d):{" "}
      <span className="font-medium text-foreground">
        {totals.cost_usd != null ? `$${totals.cost_usd.toFixed(2)}` : "not priced"}
      </span>{" "}
      · {totals.tokens_in.toLocaleString()} in / {totals.tokens_out.toLocaleString()}{" "}
      out over {totals.calls} call{totals.calls === 1 ? "" : "s"}
      {totals.unparsed_calls > 0 && (
        <span className="text-amber-700 dark:text-amber-400">
          {" "}
          · {totals.unparsed_calls} unmeasured
        </span>
      )}
      {canViewCosts && (
        <>
          {" · "}
          <Link href="/costs" className="underline underline-offset-4">
            breakdown
          </Link>
        </>
      )}
    </p>
  );
}
