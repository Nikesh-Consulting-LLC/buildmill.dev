"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "@/lib/router-with-progress";
import { Activity, ScrollText } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { AgentText } from "@/components/agent-text";
import { deriveActivity } from "@/lib/run-activity";

/** US-13.7: what the agent is doing, while it does it — the latest
 * progress note beside the stage tracker, with legible liveness (how
 * long the run has been going, how long since the worker last spoke).
 * Renders nothing when no run is in flight; degrades to liveness-only
 * when the agent hasn't narrated. */
export function LiveActivity({
  issueId,
  orgId,
  runId,
  workerName,
  claimedAt,
  lastHeartbeatAt,
  note,
  noteAt,
  lastTool,
  lastToolAt,
  variant = "full",
  subscribe = true,
}: {
  issueId: string;
  /** US-87.5: scopes the run_activity subscription, which carries no
   * issue_id of its own. */
  orgId: string;
  /** US-15.5: the claimed run, so the manager can open its full trace. */
  runId?: string;
  workerName: string | null;
  claimedAt: string | null;
  lastHeartbeatAt: string | null;
  note: string | null;
  noteAt: string | null;
  /** US-14.8: the last MCP call the factory served for this run. */
  lastTool?: string | null;
  lastToolAt?: string | null;
  /** US-15.19: `chip` is the one-line form for the sticky cockpit header;
   * the full panel lives in the Runs tab. */
  variant?: "full" | "chip";
  /** The header chip is always mounted and owns the realtime subscription;
   * the Runs-tab panel opts out so the two never open the same channel. */
  subscribe?: boolean;
}) {
  const router = useRouter();
  // A ticking clock so "running 12m · last heard 2m ago" ages without a
  // reload; the realtime subscription below refreshes the data itself.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    if (!subscribe) return;
    const supabase = createClient();
    let channel: ReturnType<typeof supabase.channel> | null = null;
    let cancelled = false;
    (async () => {
      const {
        data: { session },
      } = await supabase.auth.getSession();
      if (cancelled || !session) return;
      supabase.realtime.setAuth(session.access_token);
      channel = supabase
        .channel(`live-activity-${issueId}`)
        .on(
          "postgres_changes",
          {
            event: "INSERT",
            schema: "public",
            table: "issue_events",
            filter: `issue_id=eq.${issueId}`,
          },
          () => router.refreshSilently()
        )
        // US-14.8: the derived activity line is only "live" if its source
        // is. Without this it would age by the ticking clock and never
        // change what it says until something else refreshed the page.
        // US-87.5: named rows. `run_activity` carries no issue_id, so the
        // workspace is the narrowest filter available here — still the
        // difference between decoding this org's agent steps and every
        // org's.
        .on(
          "postgres_changes",
          {
            event: "INSERT",
            schema: "public",
            table: "run_activity",
            filter: `org_id=eq.${orgId}`,
          },
          () => router.refreshSilently()
        )
        .subscribe();
    })();
    return () => {
      cancelled = true;
      if (channel) supabase.removeChannel(channel);
    };
  }, [issueId, orgId, router, subscribe]);

  if (!claimedAt) return null;

  const mins = (iso: string | null) =>
    iso ? Math.max(0, Math.floor((now - new Date(iso).getTime()) / 60_000)) : null;
  const runningMinutes = mins(claimedAt);
  const silentMinutes = mins(lastHeartbeatAt ?? claimedAt);
  const noteMinutes = mins(noteAt);

  /** US-14.8: what the factory itself last saw, and whether the quiet has
   * outrun what this stage should need. Replaces a flat 20-minute rule
   * that read a healthy long write as a stall. */
  const activity = deriveActivity(lastTool ?? null, lastToolAt ?? null, now);
  const silentTone = activity?.overdue
    ? "text-amber-600 dark:text-amber-400"
    : "text-muted-foreground";

  // US-15.19: the header's one-line read — who holds it, whether it is still
  // moving, and a way through to the full account in the Runs tab.
  if (variant === "chip") {
    return (
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
        <span className="flex items-center gap-1.5 font-medium">
          <Activity className="size-3.5 text-muted-foreground" />
          {workerName || "A worker"} is on it
        </span>
        <span className={silentTone}>
          running {runningMinutes}m · last heard{" "}
          {silentMinutes === 0 ? "just now" : `${silentMinutes}m ago`}
        </span>
        {activity && !activity.blockedOnYou && !activity.overdue && (
          <span className="min-w-0 truncate text-muted-foreground">
            · now: {activity.doing}
          </span>
        )}
        {activity?.blockedOnYou && (
          <span className="font-medium text-amber-700 dark:text-amber-400">
            · waiting on your answer
          </span>
        )}
        {activity?.overdue && (
          <span className="font-medium text-amber-700 dark:text-amber-400">
            · quiet {activity.silentMinutes}m — longer than usual
          </span>
        )}
        {/* US-49.5: the run detail moved under History, above the timeline. */}
        <Link
          href={`?tab=history`}
          scroll={false}
          className="font-medium hover:underline"
        >
          Details
        </Link>
      </div>
    );
  }

  return (
    <div className="rounded-lg border px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
        <span className="flex items-center gap-1.5 font-medium text-foreground">
          <Activity className="size-3.5 text-muted-foreground" />
          {workerName || "A worker"} is on it
        </span>
        <span className="flex items-center gap-2">
          <span className={silentTone}>
            running {runningMinutes}m · last heard{" "}
            {silentMinutes === 0 ? "just now" : `${silentMinutes}m ago`}
          </span>
          {/* US-15.5: the full, durable trace of this run. */}
          {runId && (
            <Link
              href={`/runs/${runId}`}
              className="inline-flex items-center gap-1 font-medium text-foreground hover:underline"
            >
              <ScrollText className="size-3.5" />
              Full trace
            </Link>
          )}
        </span>
      </div>
      {/* US-14.8: derived from the run's own tool calls, so it keeps moving
          between the agent's (rare) progress notes. */}
      {activity && (
        <p className="mt-1.5 text-xs text-muted-foreground">
          {activity.blockedOnYou ? (
            <span className="font-medium text-amber-700 dark:text-amber-400">
              Waiting on your answer — see Things to Do.
            </span>
          ) : activity.overdue ? (
            <span className="font-medium text-amber-700 dark:text-amber-400">
              Last seen {activity.doing}, {activity.silentMinutes}m ago —
              longer than this stage usually goes quiet.
            </span>
          ) : (
            <>
              Now: {activity.doing}
              {activity.silentMinutes > 0 &&
                ` · ${activity.silentMinutes}m ago`}
            </>
          )}
        </p>
      )}
      {note ? (
        <div className="mt-2 text-sm">
          {/* US-14.1: agents narrate in markdown; render it as such. */}
          <AgentText clamp={160}>{note}</AgentText>
          {noteMinutes !== null && (
            <span className="text-xs text-muted-foreground">
              {noteMinutes === 0 ? "just now" : `${noteMinutes}m ago`}
            </span>
          )}
        </div>
      ) : (
        <p className="mt-2 text-sm text-muted-foreground">
          No progress note yet — liveness above is from the claim heartbeat.
        </p>
      )}
    </div>
  );
}
