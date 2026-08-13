"use client";

import { useEffect } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { createClient } from "@/lib/supabase/client";

/** US-15.5: fill the run trace live while the run works. Subscribes to the
 * org's run_trace and run_activity inserts (and the run row itself, so a
 * terminal status flip re-renders the outcome) and refreshes, debounced. */
export function RunTraceLive({ runId, orgId }: { runId: string; orgId: string }) {
  const router = useRouter();

  useEffect(() => {
    if (!orgId) return;
    const supabase = createClient();
    let channel: ReturnType<typeof supabase.channel> | null = null;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const refresh = () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => router.refreshSilently(), 400);
    };

    async function subscribe() {
      const {
        data: { session },
      } = await supabase.auth.getSession();
      if (!session || cancelled) return;
      await supabase.realtime.setAuth(session.access_token);
      channel = supabase
        .channel(`run-trace-${runId}`, { config: { private: false } })
        .on(
          "postgres_changes",
          {
            event: "INSERT",
            schema: "public",
            table: "run_trace",
            filter: `run_id=eq.${runId}`,
          },
          refresh
        )
        .on(
          "postgres_changes",
          {
            event: "INSERT",
            schema: "public",
            table: "run_activity",
            filter: `run_id=eq.${runId}`,
          },
          refresh
        )
        .on(
          "postgres_changes",
          {
            event: "UPDATE",
            schema: "public",
            table: "runs",
            filter: `id=eq.${runId}`,
          },
          refresh
        )
        .subscribe();
    }
    subscribe();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      if (channel) supabase.removeChannel(channel);
    };
  }, [runId, orgId, router]);

  return null;
}
