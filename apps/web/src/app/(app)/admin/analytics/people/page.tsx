"use client";

// US-62.4 / US-62.5 / US-62.6: a human's work, in one report. Two tabs over
// the same window/org/project filters: Activity (approved/merged, test
// cases executed, comments — real counts, each already attributed to a real
// user id in its own source table — plus a "Reviewing" column, the real,
// pause-aware active time us-62.6 instruments) and Gate latency (how long
// each gate waited for a decision — explicitly wait time, never active
// effort, kept deliberately distinct from the Reviewing column here).

import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

type ActivityRow = {
  user_id: string;
  user_label: string;
  org_id: string;
  project_id: string;
  project_label: string;
  approved: number;
  test_pass: number;
  test_fail: number;
  comments: number;
  reviewing_ms: number;
};

type GateRow = {
  gate: string;
  user_id: string;
  user_label: string;
  org_id: string;
  project_id: string;
  decisions: number;
  avg_seconds: number | null;
  min_seconds: number | null;
  max_seconds: number | null;
  p95_seconds: number | null;
};

type AutoApproved = { gate: string; org_id: string; project_id: string; auto_approved: number };

const WINDOWS = [
  { days: 7, label: "7 days" },
  { days: 30, label: "30 days" },
  { days: 90, label: "90 days" },
];

const GATE_LABELS: Record<string, string> = {
  prd: "PRD",
  plan: "Plan",
  "code-review": "Code review",
  elaboration: "Elaboration",
  "qa-signoff": "QA sign-off",
  promotion: "Promotion",
};

function elapsed(seconds: number | null) {
  if (seconds == null) return "—";
  if (seconds < 90) return `${seconds}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)}h`;
  return `${(seconds / 86400).toFixed(1)}d`;
}

function reviewingTime(activeMs: number) {
  if (!activeMs) return "—";
  return elapsed(Math.round(activeMs / 1000));
}

export default function AdminPeopleAnalyticsPage() {
  const [tab, setTab] = useState<"activity" | "latency">("activity");
  const [days, setDays] = useState(30);
  const [activity, setActivity] = useState<ActivityRow[] | null>(null);
  const [gates, setGates] = useState<{ rows: GateRow[]; auto_approved: AutoApproved[] } | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      if (tab === "activity") {
        const res = await apiFetch(`/api/v1/admin/user-activity?days=${days}`);
        setActivity(res?.rows ?? []);
      } else {
        const res = await apiFetch(`/api/v1/admin/gate-latency?days=${days}`);
        setGates({ rows: res?.rows ?? [], auto_approved: res?.auto_approved ?? [] });
      }
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [tab, days]);

  useEffect(() => {
    void load();
  }, [load]);

  const autoCountFor = (gate: string) =>
    (gates?.auto_approved ?? [])
      .filter((a) => a.gate === gate)
      .reduce((sum, a) => sum + a.auto_approved, 0);

  return (
    <div className="flex w-full flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">People</h1>
        <p className="max-w-3xl text-sm text-muted-foreground">
          What each human did, and how long each gate waited for them. Every
          count below comes from a table that already attributes it to a
          real person — none of it is a time estimate.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          variant={tab === "activity" ? "default" : "outline"}
          onClick={() => setTab("activity")}
        >
          Activity
        </Button>
        <Button
          size="sm"
          variant={tab === "latency" ? "default" : "outline"}
          onClick={() => setTab("latency")}
        >
          Gate latency
        </Button>
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

      {tab === "activity" ? (
        activity === null ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : activity.length === 0 ? (
          <div className="rounded-md border border-dashed p-8 text-sm text-muted-foreground">
            No human activity in this window.
          </div>
        ) : (
          <div className="overflow-x-auto rounded-md border">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b bg-muted/40 text-xs">
                  <th className="px-3 py-2 font-medium">Person</th>
                  <th className="px-3 py-2 font-medium">Project</th>
                  <th className="px-3 py-2 text-right font-medium">Approved / merged</th>
                  <th className="px-3 py-2 text-right font-medium">Test cases</th>
                  <th className="px-3 py-2 text-right font-medium">Comments</th>
                  <th
                    className="px-3 py-2 text-right font-medium"
                    title="Real, pause-aware active time in the PRD/plan/code-review actions and artifact editing (US-62.6) — never queue-inclusive wait time (see the Gate latency tab for that)."
                  >
                    Reviewing
                  </th>
                </tr>
              </thead>
              <tbody>
                {activity.map((r) => (
                  <tr key={`${r.user_id}-${r.project_id}`} className="border-b last:border-0">
                    <td className="px-3 py-1.5">{r.user_label}</td>
                    <td className="px-3 py-1.5 text-muted-foreground">{r.project_label}</td>
                    <td className="px-3 py-1.5 text-right font-mono text-xs">{r.approved}</td>
                    <td className="px-3 py-1.5 text-right font-mono text-xs">
                      {r.test_pass + r.test_fail}{" "}
                      {r.test_fail > 0 && (
                        <span className="text-destructive">({r.test_fail} failed)</span>
                      )}
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono text-xs">{r.comments}</td>
                    <td className="px-3 py-1.5 text-right font-mono text-xs">
                      {reviewingTime(r.reviewing_ms)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      ) : gates === null ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : gates.rows.length === 0 ? (
        <div className="rounded-md border border-dashed p-8 text-sm text-muted-foreground">
          No gate decisions in this window.
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          <p className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300">
            These are wait times — from the item becoming ready to the decision
            being recorded — not active effort. An item that sat untouched in a
            queue for two days reads the same as one reviewed instantly two
            days late.
          </p>
          <div className="overflow-x-auto rounded-md border">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b bg-muted/40 text-xs">
                  <th className="px-3 py-2 font-medium">Gate</th>
                  <th className="px-3 py-2 font-medium">Person</th>
                  <th className="px-3 py-2 text-right font-medium">Decisions</th>
                  <th className="px-3 py-2 text-right font-medium">Avg</th>
                  <th className="px-3 py-2 text-right font-medium">Min</th>
                  <th className="px-3 py-2 text-right font-medium">Max</th>
                  <th className="px-3 py-2 text-right font-medium">p95</th>
                </tr>
              </thead>
              <tbody>
                {gates.rows.map((r, i) => (
                  <tr key={`${r.gate}-${r.user_id}-${i}`} className="border-b last:border-0">
                    <td className="px-3 py-1.5">
                      <Badge variant="outline" className="text-[11px]">
                        {GATE_LABELS[r.gate] ?? r.gate}
                      </Badge>
                    </td>
                    <td className="px-3 py-1.5">{r.user_label}</td>
                    <td className="px-3 py-1.5 text-right font-mono text-xs">{r.decisions}</td>
                    <td className="px-3 py-1.5 text-right font-mono text-xs">
                      {elapsed(r.avg_seconds)}
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono text-xs">
                      {elapsed(r.min_seconds)}
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono text-xs">
                      {elapsed(r.max_seconds)}
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono text-xs">
                      {elapsed(r.p95_seconds)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {Object.keys(GATE_LABELS)
            .filter((g) => autoCountFor(g) > 0)
            .length > 0 && (
            <p className="text-xs text-muted-foreground">
              Auto-approved, excluded above:{" "}
              {Object.keys(GATE_LABELS)
                .filter((g) => autoCountFor(g) > 0)
                .map((g) => `${GATE_LABELS[g]} (${autoCountFor(g)})`)
                .join(", ")}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
