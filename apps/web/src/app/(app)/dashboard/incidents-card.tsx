"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "@/lib/router-with-progress";
import {
  AlertTriangle,
  Bot,
  ChevronDown,
  ChevronRight,
  Loader2,
  X,
} from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { AgentText } from "@/components/agent-text";
import { Badge } from "@/components/ui/badge";
import { RUN_KIND_LABELS, type RunKind } from "@/lib/run-kinds";
import type { IncidentRow } from "./data";

function runKindLabel(kind: string | null): string | null {
  if (!kind) return null;
  return RUN_KIND_LABELS[kind as RunKind] ?? kind;
}

/** US-15.18: the "Runs that died holding their claim" surface, collapsed to a
 * glanceable count that expands on demand and can be cleared. Clearing is an
 * org-wide acknowledgement (dashboard_incident_dismissals, keyed by the
 * issue_events.id) — it hides the incident, it does not touch the run. A newer
 * death on the same issue is a new event id, so it reappears. */
export function IncidentsCard({ incidents }: { incidents: IncidentRow[] }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [cleared, setCleared] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const visible = incidents.filter((i) => !cleared.has(i.id));
  // The banner disappears entirely once nothing is left to show.
  if (visible.length === 0) return null;

  async function dismiss(rows: IncidentRow[]) {
    if (rows.length === 0) return;
    setBusy(true);
    setError(null);
    const ids = new Set(rows.map((r) => r.id));
    // Optimistic: the rows leave the list immediately.
    setCleared((prev) => new Set([...prev, ...ids]));
    const supabase = createClient();
    const { error: dbError } = await supabase
      .from("dashboard_incident_dismissals")
      .upsert(
        rows.map((r) => ({ org_id: r.orgId, event_id: r.id })),
        { onConflict: "org_id,event_id", ignoreDuplicates: true }
      );
    if (dbError) {
      // Revert the optimistic removal and surface the failure.
      setCleared((prev) => {
        const next = new Set(prev);
        ids.forEach((id) => next.delete(id));
        return next;
      });
      setError("Couldn't clear that. Try again.");
      setBusy(false);
      return;
    }
    setBusy(false);
    // Reconcile the rest of the page (the server now filters these out too).
    router.refresh();
  }

  const n = visible.length;

  return (
    <div className="rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-900 dark:border-red-900 dark:bg-red-950/70 dark:text-red-200">
      <div className="flex items-center justify-between gap-3">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex min-w-0 flex-1 items-center gap-2 text-left font-semibold"
          aria-expanded={open}
        >
          <AlertTriangle className="size-4 shrink-0" />
          <span className="min-w-0 truncate">
            {n} run{n === 1 ? "" : "s"} died holding their claim
          </span>
          <span className="shrink-0 text-xs font-normal opacity-80">
            · last 48h
          </span>
          {open ? (
            <ChevronDown className="size-4 shrink-0 opacity-70" />
          ) : (
            <ChevronRight className="size-4 shrink-0 opacity-70" />
          )}
        </button>
        <button
          type="button"
          onClick={() => dismiss(visible)}
          disabled={busy}
          className="inline-flex shrink-0 items-center gap-1 rounded-md border border-red-300 px-2 py-1 text-xs font-medium transition-colors hover:bg-red-100 disabled:opacity-60 dark:border-red-800 dark:hover:bg-red-900/50"
          title="Acknowledge all — hides them for the whole team"
        >
          {busy && <Loader2 className="size-3 animate-spin" />}
          Clear all
        </button>
      </div>

      {open && (
        <div className="mt-3 flex flex-col gap-2">
          {/* US-57.15: each death is its own card — who held it, what stage,
              and the agent's own account in full, not a single truncated
              line nobody could read past its first thirty characters. */}
          {visible.map((inc) => (
            <div
              key={inc.id}
              className="rounded-md border border-red-200 bg-white/50 p-2.5 dark:border-red-900/70 dark:bg-red-950/30"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                  <Link
                    href={`/issues/${inc.issueId}?from=dashboard`}
                    className="min-w-0 truncate font-medium underline-offset-4 hover:underline"
                  >
                    {inc.title}
                  </Link>
                  {runKindLabel(inc.runKind) && (
                    <Badge variant="outline" className="border-red-300 text-xs dark:border-red-800">
                      {runKindLabel(inc.runKind)}
                    </Badge>
                  )}
                  {inc.worker && (
                    <span className="inline-flex items-center gap-1 text-xs opacity-80">
                      <Bot className="size-3" />
                      {inc.worker}
                    </span>
                  )}
                  <span className="text-xs opacity-70">{inc.age}</span>
                </div>
                <button
                  type="button"
                  onClick={() => dismiss([inc])}
                  disabled={busy}
                  aria-label={`Clear "${inc.title}"`}
                  title="Acknowledge — hides it for the whole team"
                  className="shrink-0 rounded p-0.5 opacity-70 transition-colors hover:bg-red-100 hover:opacity-100 disabled:opacity-40 dark:hover:bg-red-900/50"
                >
                  <X className="size-3.5" />
                </button>
              </div>
              <AgentText
                clamp={140}
                className="mt-1.5 text-xs text-red-900/90 dark:text-red-200/90"
              >
                {inc.reason}
              </AgentText>
            </div>
          ))}
          {error && (
            <span className="text-xs text-destructive">{error}</span>
          )}
        </div>
      )}
    </div>
  );
}
