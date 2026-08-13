"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createClient } from "@/lib/supabase/client";
import { issueMatchesQuery } from "@/lib/issue-search";
import {
  HIGHLIGHT_MS,
  nextChangedSet,
} from "@/lib/recent-changes";
import { type HubEpic, type ViewIssue } from "./issue-view-types";

/**
 * US-8.1: keep the cross-project work-item list live. Subscribes to every
 * issue the user can see (org-scoped by RLS) and keeps the full set; callers
 * narrow to the selected projects. Realtime payloads don't carry the epic
 * join, so inserts/updates are enriched from the epic map. When `searchQuery`
 * is set, rows that don't match are dropped.
 */
export function useHubIssues(
  initialIssues: ViewIssue[],
  searchQuery: string,
  epics: HubEpic[],
  orgId: string
) {
  const [issues, setIssues] = useState<ViewIssue[]>(initialIssues);

  // US-87.12: which rows just changed, so each one can say so locally. A
  // realtime refresh is deliberately silent at the page level (see
  // `refreshSilently` in lib/router-with-progress.ts) — this is the signal
  // that replaces it, scoped to the thing that actually moved.
  //
  // The live set is held in a ref and mirrored to state: the storm check has
  // to read the current size and clear pending timers, and doing that inside
  // a state updater would be a side effect React is free to run twice.
  const changedRef = useRef<Set<string>>(new Set());
  const timers = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());
  const [recentlyChanged, setRecentlyChanged] = useState<ReadonlySet<string>>(
    new Set()
  );

  const clearAllTimers = useCallback(() => {
    for (const t of timers.current.values()) clearTimeout(t);
    timers.current.clear();
  }, []);

  const markChanged = useCallback(
    (id: string) => {
      const next = nextChangedSet(changedRef.current, id);
      if (next === null) {
        // A batch dispatch moved everything at once. Twenty rows pulsing
        // together is the list flashing, not a signal — stand down.
        clearAllTimers();
        changedRef.current = new Set();
        setRecentlyChanged(new Set());
        return;
      }
      changedRef.current = next;
      setRecentlyChanged(new Set(next));

      const existing = timers.current.get(id);
      if (existing) clearTimeout(existing);
      timers.current.set(
        id,
        setTimeout(() => {
          timers.current.delete(id);
          changedRef.current.delete(id);
          setRecentlyChanged(new Set(changedRef.current));
        }, HIGHLIGHT_MS)
      );
    },
    [clearAllTimers]
  );

  useEffect(() => clearAllTimers, [clearAllTimers]);

  useEffect(() => {
    setIssues(initialIssues);
    // Sync when the server sends a new result set (search change / refresh).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchQuery, initialIssues]);

  useEffect(() => {
    const supabase = createClient();
    let channel: ReturnType<typeof supabase.channel> | null = null;
    let cancelled = false;

    const epicById = new Map(epics.map((e) => [e.id, e]));
    // Backfill the epic join a realtime row lacks so grouping stays correct.
    function enrich(raw: ViewIssue): ViewIssue {
      const epic = raw.epic_id ? epicById.get(raw.epic_id) : undefined;
      return {
        ...raw,
        parent_id: raw.parent_id ?? null,
        epic_id: raw.epic_id ?? null,
        epic_title: epic?.title ?? raw.epic_title ?? null,
        epic_number: epic?.number ?? raw.epic_number ?? null,
      };
    }

    async function subscribe() {
      const {
        data: { session },
      } = await supabase.auth.getSession();
      if (!session || cancelled) return;
      await supabase.realtime.setAuth(session.access_token);

      channel = supabase
        .channel(`issues-hub-views-${orgId}`, {
          config: { private: false },
        })
        .on(
          "postgres_changes",
          {
            event: "*",
            schema: "public",
            table: "issues",
            // US-87.5: named rows, not every row. Unfiltered, this decoded
            // and RLS-evaluated EVERY issue change in every workspace for
            // every hub subscriber, then discarded most of it in JS below.
            // RLS already made those rows unreachable; the filter is what
            // stops the database doing the work to prove it.
            filter: `org_id=eq.${orgId}`,
          },
          (payload) => {
            const q = searchQuery.trim();

            if (payload.eventType === "INSERT") {
              const raw = payload.new as ViewIssue & {
                abandoned_at?: string | null;
              };
              if (raw.abandoned_at) return;
              if (q && !issueMatchesQuery(raw, q)) return;
              markChanged(raw.id);
              setIssues((prev) =>
                prev.some((x) => x.id === raw.id)
                  ? prev
                  : [enrich(raw), ...prev]
              );
            } else if (payload.eventType === "UPDATE") {
              const raw = payload.new as ViewIssue & {
                abandoned_at?: string | null;
              };
              if (raw.abandoned_at) {
                setIssues((prev) => prev.filter((x) => x.id !== raw.id));
                return;
              }
              if (q && !issueMatchesQuery(raw, q)) {
                setIssues((prev) => prev.filter((x) => x.id !== raw.id));
                return;
              }
              markChanged(raw.id);
              setIssues((prev) => {
                const existing = prev.find((x) => x.id === raw.id);
                const enriched = enrich(raw);
                const next: ViewIssue = {
                  ...enriched,
                  // If the epic map couldn't resolve a title, keep the one we
                  // already had rather than blanking it.
                  epic_title: enriched.epic_title ?? existing?.epic_title ?? null,
                };
                if (!existing) return [next, ...prev];
                return prev.map((x) => (x.id === raw.id ? { ...x, ...next } : x));
              });
            } else if (payload.eventType === "DELETE") {
              const raw = payload.old as Partial<ViewIssue>;
              setIssues((prev) => prev.filter((x) => x.id !== raw.id));
            }
          }
        )
        .subscribe();
    }

    subscribe();

    return () => {
      cancelled = true;
      if (channel) supabase.removeChannel(channel);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchQuery, epics, orgId, markChanged]);

  return [issues, setIssues, recentlyChanged] as const;
}
