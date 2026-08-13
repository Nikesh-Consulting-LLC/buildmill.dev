"use client";

// US-62.6: a review dialog times itself. Every existing timestamp pair
// (us-62.5's gate latency) measures queue-inclusive wait time, not active
// effort — this hook records the real thing: `active_ms` pauses while the
// tab is backgrounded or the user has been idle, so a dialog left open
// during a meeting doesn't inflate anyone's "time reviewing" number.
//
// Two call shapes, same hook:
//   - A review action component (PrdReviewActions/PlanReviewActions/
//     ReviewActions) passes `active=true` for its whole mount lifetime —
//     that lifetime already equals "how long this item sat in front of a
//     manager while reviewable" (US-27.11-style: the component only
//     renders while the issue is in that gate's status).
//   - An artifact-edit component passes `active={editing}` — an inline
//     expand/collapse boolean, not a Dialog, but the same open→close shape.

import { useEffect, useRef } from "react";
import { createClient } from "@/lib/supabase/client";

const IDLE_TIMEOUT_MS = 2 * 60 * 1000;
// Discards an accidental open (a misclick, a dialog opened and immediately
// closed) rather than recording a session that says nothing real.
const MIN_SESSION_MS = 2000;
const ACTIVITY_EVENTS = ["mousemove", "keydown", "mousedown", "wheel"] as const;

export type ActivitySessionKind = "prd" | "plan" | "code-review" | "artifact-edit";

export function useActivitySession(
  active: boolean,
  kind: ActivitySessionKind,
  issueId: string,
) {
  const state = useRef({
    startedAt: 0,
    activeMs: 0,
    lastTick: 0,
    paused: false,
    idleTimer: null as ReturnType<typeof setTimeout> | null,
  });

  useEffect(() => {
    if (!active || !issueId) return;

    const s = state.current;
    s.startedAt = Date.now();
    s.activeMs = 0;
    s.lastTick = Date.now();
    s.paused = false;

    function tick() {
      if (!s.paused) s.activeMs += Date.now() - s.lastTick;
      s.lastTick = Date.now();
    }

    function armIdleTimer() {
      if (s.idleTimer) clearTimeout(s.idleTimer);
      s.idleTimer = setTimeout(() => {
        tick();
        s.paused = true;
      }, IDLE_TIMEOUT_MS);
    }

    function onActivity() {
      tick();
      s.paused = false;
      armIdleTimer();
    }

    function onVisibility() {
      tick();
      s.paused = document.hidden;
      if (!document.hidden) armIdleTimer();
    }

    ACTIVITY_EVENTS.forEach((e) => window.addEventListener(e, onActivity));
    document.addEventListener("visibilitychange", onVisibility);
    armIdleTimer();

    return () => {
      tick();
      ACTIVITY_EVENTS.forEach((e) => window.removeEventListener(e, onActivity));
      document.removeEventListener("visibilitychange", onVisibility);
      if (s.idleTimer) clearTimeout(s.idleTimer);

      const endedAt = Date.now();
      const startedAt = s.startedAt;
      const activeMs = Math.round(s.activeMs);
      if (activeMs < MIN_SESSION_MS) return;

      const supabase = createClient();
      // Fire-and-forget: a manager's review must never wait on, or be
      // blocked by, this write. org_id is resolved here, not threaded down
      // as a prop through five different components — every one of them
      // already has an issueId, and RLS already scopes this read to org
      // members, the same as every other per-issue client read in the app.
      void Promise.all([
        supabase.auth.getUser(),
        supabase.from("issues").select("org_id").eq("id", issueId).maybeSingle(),
      ]).then(([{ data: userData }, { data: issueData }]) => {
        const userId = userData.user?.id;
        const orgId = issueData?.org_id;
        if (!userId || !orgId) return;
        void supabase.from("user_activity_sessions").insert({
          org_id: orgId,
          user_id: userId,
          issue_id: issueId,
          kind,
          started_at: new Date(startedAt).toISOString(),
          ended_at: new Date(endedAt).toISOString(),
          active_ms: activeMs,
        });
      });
    };
  }, [active, kind, issueId]);
}
