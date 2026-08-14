"use client";

/**
 * US-37.1: a project's budget, where project configuration lives.
 *
 * Money was metered per project from the first line of us-33.1 — every
 * llm_usage row carries project_id. What did not exist was a number to compare
 * it against. This is that number, plus what has been spent against it.
 *
 * Two things a budget cannot see are named on the card rather than left for the
 * manager to discover: spend on calls with no run behind them (the brain, TLDR,
 * complexity scoring) carries a null project_id and belongs to no project, and
 * a model with no rate spends real money that contributes $0 to every total.
 */

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import Link from "next/link";
import { Wallet, AlertTriangle } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { budgetState, budgetLabel, money, percent } from "@/lib/budget";
import { cn } from "@/lib/utils";

export function BudgetCard({
  projectId,
  budgetEnabled,
  budgetUsd,
  budgetStartedAt,
  spent,
  unmeasured,
  canManage,
}: {
  projectId: string;
  budgetEnabled: boolean;
  budgetUsd: number | null;
  budgetStartedAt: string | null;
  spent: number;
  unmeasured: number;
  canManage: boolean;
}) {
  const router = useRouter();
  const [enabled, setEnabled] = useState(budgetEnabled);
  const [amount, setAmount] = useState(budgetUsd == null ? "" : String(budgetUsd));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const state = budgetState(
    { budget_enabled: enabled, budget_usd: amount === "" ? null : Number(amount) },
    { spent, unmeasured }
  );
  const label = budgetLabel(state);

  async function persist(patch: Record<string, unknown>) {
    setBusy(true);
    setError(null);
    const supabase = createClient();
    const { error } = await supabase
      .from("projects")
      .update(patch)
      .eq("id", projectId);
    setBusy(false);
    if (error) {
      setError(error.message);
      return;
    }
    router.refresh();
  }

  async function toggle(next: boolean) {
    setEnabled(next);
    // Stamp the counter the first time a budget is switched on, so spend that
    // predates the decision does not exhaust it on day one. Turning it back on
    // later keeps the counter it already had — re-stamping silently would
    // forgive spend the manager never chose to forgive.
    await persist(
      next && !budgetStartedAt
        ? { budget_enabled: true, budget_started_at: new Date().toISOString() }
        : { budget_enabled: next }
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Wallet className="size-4" />
          Budget
        </CardTitle>
        <CardDescription>
          What this project may spend on model calls. An exhausted budget stops
          new work from starting; runs already going finish. Raising the number
          is all it takes to get moving again.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <span className="font-mono text-2xl tabular-nums">
            {money(state.spent)}
          </span>
          {state.budget != null && (
            <span className="text-sm text-muted-foreground">
              of {money(state.budget)} · {percent(state.fraction ?? 0)} used
            </span>
          )}
          {label && (
            <span
              className={cn(
                "rounded-full border px-2 py-0.5 text-xs font-medium",
                state.exhausted
                  ? "border-destructive/40 bg-destructive/10 text-destructive"
                  : state.near
                    ? "border-amber-400/50 bg-amber-50 text-amber-800 dark:bg-amber-950/60 dark:text-amber-300"
                    : "text-muted-foreground"
              )}
            >
              {label}
            </span>
          )}
        </div>

        {state.budget != null && (
          <div
            className="h-2 w-full overflow-hidden rounded-full bg-muted"
            role="img"
            aria-label={`${percent(state.fraction ?? 0)} of budget used`}
          >
            <div
              className={cn(
                "h-full rounded-full transition-[width]",
                state.exhausted
                  ? "bg-destructive"
                  : state.near
                    ? "bg-amber-500"
                    : "bg-primary"
              )}
              style={{ width: `${Math.min(100, (state.fraction ?? 0) * 100)}%` }}
            />
          </div>
        )}

        <p className="text-xs text-muted-foreground">
          Counting from{" "}
          {budgetStartedAt
            ? new Date(budgetStartedAt).toLocaleDateString()
            : "the project's first metered call"}
          .{" "}
          {unmeasured > 0 && (
            <>
              <span className="text-amber-700 dark:text-amber-400">
                {unmeasured} {unmeasured === 1 ? "call" : "calls"} on a model
                with no rate are not in this figure
              </span>{" "}
              —{" "}
              <Link
                href="/settings/llm-providers"
                className="underline underline-offset-4"
              >
                set its rate
              </Link>{" "}
              so they count.{" "}
            </>
          )}
          Model calls with no run behind them — summaries, scoring, the
          server-side brain — belong to no project and are never counted here.
        </p>

        {canManage ? (
          <div className="grid gap-3 border-t pt-3">
            <label className="flex cursor-pointer items-start gap-3">
              <Checkbox
                checked={enabled}
                onCheckedChange={(c) => void toggle(!!c)}
                disabled={busy}
                className="mt-0.5"
              />
              <span className="min-w-0">
                <span className="block text-sm font-medium">
                  Hold work to a budget
                </span>
                <span className="block text-xs text-muted-foreground">
                  Off by default. While off, spend is still measured — it just
                  never stops anything.
                </span>
              </span>
            </label>

            <div className="flex flex-wrap items-end gap-2">
              <div className="grid gap-1">
                <Label className="text-xs text-muted-foreground">
                  Budget (USD)
                </Label>
                <input
                  type="number"
                  min={0}
                  step={1}
                  value={amount}
                  disabled={busy}
                  onChange={(e) => setAmount(e.target.value)}
                  placeholder="none"
                  className="w-32 rounded-md border bg-background px-2 py-1 text-sm"
                />
              </div>
              <Button
                size="sm"
                disabled={busy || amount === ""}
                onClick={() => void persist({ budget_usd: Number(amount) })}
              >
                {busy ? "Saving…" : "Save budget"}
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={() =>
                  void persist({ budget_started_at: new Date().toISOString() })
                }
                title="Start counting spend from now. The budget itself is unchanged."
              >
                Reset the counter
              </Button>
            </div>
            {state.exhausted && (
              <p className="flex items-start gap-2 text-xs text-destructive">
                <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
                <span>
                  New runs on this project are being refused. Raise the budget or
                  reset the counter.
                </span>
              </p>
            )}
            {error && <p className="text-sm text-destructive">{error}</p>}
          </div>
        ) : (
          <p className="border-t pt-3 text-xs text-muted-foreground">
            Only a project manager can set this project&apos;s budget.
          </p>
        )}
      </CardContent>
    </Card>
  );
}
