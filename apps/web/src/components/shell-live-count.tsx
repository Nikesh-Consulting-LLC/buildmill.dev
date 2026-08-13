"use client";

import { useEffect } from "react";
import { usePathname } from "next/navigation";
import { useRouter } from "@/lib/router-with-progress";
import { createClient } from "@/lib/supabase/client";

// US-6.1: the shell's live nerve. It keeps the browser tab title carrying the
// pending-decision count and, on any change to a pipeline table for this org,
// re-renders the current route (page + layout) so Things to Do updates in
// place and the sidebar badge stays honest — no manual refresh. It renders
// nothing itself.

const TABLES = [
  "issues",
  "runs",
  "deployment_runs",
  "clarifications",
  "releases",
];

export function ShellLiveCount({
  count,
  orgId,
}: {
  count: number;
  orgId: string;
}) {
  const router = useRouter();
  const pathname = usePathname();

  // Re-apply the "(N) " title prefix whenever the count changes or a per-page
  // title takes over on navigation. A MutationObserver catches Next's own
  // title writes; setting an already-correct title is a no-op, so it settles.
  useEffect(() => {
    const apply = () => {
      const base = document.title.replace(/^\(\d+\)\s*/, "");
      const desired = count > 0 ? `(${count}) ${base}` : base;
      if (document.title !== desired) document.title = desired;
    };
    apply();
    const titleEl = document.querySelector("title");
    if (!titleEl) return;
    const observer = new MutationObserver(apply);
    observer.observe(titleEl, { childList: true });
    return () => observer.disconnect();
  }, [count, pathname]);

  useEffect(() => {
    if (!orgId) return;
    const supabase = createClient();
    let channel: ReturnType<typeof supabase.channel> | null = null;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    // Collapse a burst of events into one refresh.
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

      let ch = supabase.channel(`things-to-do-${orgId}`, {
        config: { private: false },
      });
      for (const table of TABLES) {
        ch = ch.on(
          "postgres_changes",
          {
            event: "*",
            schema: "public",
            table,
            filter: `org_id=eq.${orgId}`,
          },
          refresh
        );
      }
      channel = ch.subscribe();
    }

    subscribe();

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      if (channel) supabase.removeChannel(channel);
    };
  }, [orgId, router]);

  return null;
}
