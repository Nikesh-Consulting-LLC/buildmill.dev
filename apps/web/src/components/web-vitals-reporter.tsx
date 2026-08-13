"use client";

// US-62.7: a page says how long it took to load. No client-side performance
// capture existed anywhere in the app before this — `useReportWebVitals` is
// Next's own built-in hook for exactly this (CLS/LCP/INP/TTFB/FCP, the same
// metrics any RUM tool measures), so no new npm dependency is needed. Fire-
// and-forget, mounted once in the root (app) layout — measuring must never
// add latency or a visible failure to the page it's measuring.

import { useCallback } from "react";
import { useReportWebVitals } from "next/web-vitals";
import { createClient } from "@/lib/supabase/client";
import { normalizeRoute } from "@/lib/normalize-route";

export function WebVitalsReporter({
  orgId,
  userId,
}: {
  orgId: string | null;
  userId: string;
}) {
  // useReportWebVitals requires a stable callback reference (per Next's own
  // docs) or it reports duplicated data — orgId/userId never change within a
  // session, so this identity is stable for the component's whole lifetime.
  const report = useCallback(
    (metric: { name: string; value: number; navigationType: string }) => {
      const supabase = createClient();
      void supabase.from("client_perf_events").insert({
        org_id: orgId,
        user_id: userId,
        route: normalizeRoute(window.location.pathname),
        metric: metric.name,
        value: metric.value,
        navigation_type: metric.navigationType,
      });
    },
    [orgId, userId]
  );

  useReportWebVitals(report);
  return null;
}
