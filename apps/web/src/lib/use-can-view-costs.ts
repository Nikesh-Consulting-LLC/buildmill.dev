"use client";

// us-95.1 AC6: no link may lead a member to a locked door. Client components
// that want to offer a "breakdown" link into /costs resolve the caller's
// `view_costs` capability here — through the same role_capabilities grid the
// server gate reads, so the link and the door agree.
//
// The answer is cached per browser tab (module scope, keyed by org): it
// changes only when a role or the grid changes, and the server gate stays
// authoritative regardless — a stale `true` here costs one polite turn-away,
// never an exposure.

import { useEffect, useState } from "react";
import { createClient } from "@/lib/supabase/client";

const cache = new Map<string, boolean>();

export function useCanViewCosts(orgId: string): boolean {
  const [fetched, setFetched] = useState<Record<string, boolean>>({});

  useEffect(() => {
    if (cache.has(orgId)) return;
    let cancelled = false;
    (async () => {
      const supabase = createClient();
      const {
        data: { user },
      } = await supabase.auth.getUser();
      if (!user) return;
      const { data: membership } = await supabase
        .from("organization_members")
        .select("role")
        .eq("org_id", orgId)
        .eq("user_id", user.id)
        .maybeSingle();
      if (!membership?.role) return;
      const { data: grid } = await supabase
        .from("role_capabilities")
        .select("allowed")
        .eq("role", membership.role)
        .eq("capability", "view_costs")
        .maybeSingle();
      const allowed = !!grid?.allowed;
      cache.set(orgId, allowed);
      if (!cancelled) setFetched((prev) => ({ ...prev, [orgId]: allowed }));
    })();
    return () => {
      cancelled = true;
    };
  }, [orgId]);

  // The cache is read at render time so a repeat mount answers immediately
  // with no state churn; the effect only ever fills a miss.
  return cache.get(orgId) ?? fetched[orgId] ?? false;
}
