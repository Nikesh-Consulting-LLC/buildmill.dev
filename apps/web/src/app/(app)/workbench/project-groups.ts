"use client";

// US-91.4: the Dispatch tab groups by project, and a project the manager is
// not working on folds away.
//
// Project is the OUTER level deliberately. The tab already groups by what the
// row needs (Dispatch, Review, QA sign-off, …); nesting project inside those
// would put the same project on screen five times and make "fold away
// everything for this project" five clicks. The manager's sentence is "clear
// Build Mill, then look at the other two", so project wraps the lot.

import { useCallback, useEffect, useState } from "react";

export type ProjectBucket<T> = {
  id: string;
  name: string;
  items: T[];
};

/** Bucket rows by their project, ordered by project name so the tab does not
 *  reshuffle between loads. Rows keep their order within a bucket. */
export function bucketByProject<T>(
  items: T[],
  keyOf: (item: T) => { id: string; name: string }
): ProjectBucket<T>[] {
  const byId = new Map<string, ProjectBucket<T>>();
  for (const item of items) {
    const { id, name } = keyOf(item);
    const bucket = byId.get(id) ?? { id, name, items: [] };
    bucket.items.push(item);
    byId.set(id, bucket);
  }
  return [...byId.values()].sort((a, b) => a.name.localeCompare(b.name));
}

/** US-91.4 AC5: one project renders flat — no grouping chrome at all. The same
 *  rule `nestAgentItems` applies to a feature owning a single row: a
 *  single-project workspace must not pay for a feature it cannot use. */
export function shouldGroup<T>(buckets: ProjectBucket<T>[]): boolean {
  return buckets.length > 1;
}

/** Which projects the manager has folded, per section, remembered across
 *  reloads and across the dashboard's own `router.refresh()` after a
 *  dispatch. Collapsing is a view state — it never changes a count. */
export function useCollapsedProjects(storageKey: string) {
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set());

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(storageKey);
      if (raw) setCollapsed(new Set(JSON.parse(raw) as string[]));
    } catch {
      // A malformed or unreadable preference is not worth a broken tab.
    }
  }, [storageKey]);

  const toggle = useCallback(
    (projectId: string) => {
      setCollapsed((prev) => {
        const next = new Set(prev);
        if (next.has(projectId)) next.delete(projectId);
        else next.add(projectId);
        try {
          window.localStorage.setItem(storageKey, JSON.stringify([...next]));
        } catch {
          // Preference storage is a convenience, never a requirement.
        }
        return next;
      });
    },
    [storageKey]
  );

  return { collapsed, toggle };
}

export const IN_PROGRESS_COLLAPSE_KEY = "dashboard.inProgress.collapsedProjects";
export const WAITING_COLLAPSE_KEY = "dashboard.dispatch.collapsedProjects";
