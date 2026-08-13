"use client";

import { useEffect, useState } from "react";
import { usePathname, useSearchParams } from "next/navigation";
import { useRouter } from "@/lib/router-with-progress";
import {
  Activity,
  BookOpen,
  Clock,
  FileText,
  FolderOpen,
  ListChecks,
  ListTree,
  MessagesSquare,
  PencilRuler,
  Rocket,
} from "lucide-react";
import { cn } from "@/lib/utils";
import {
  LEGACY_PANEL_TO_TAB,
  normalizeTab,
  tabLabel,
  type WorkItemTab,
} from "./work-item-tab-config";

const ICONS: Record<WorkItemTab, typeof FileText> = {
  overview: FileText,
  prd: BookOpen,
  stories: ListTree,
  wireframe: PencilRuler,
  plan: ListChecks,
  documents: FolderOpen,
  discussion: MessagesSquare,
  history: Clock,
  release: Rocket,
};

/** US-15.19/15.20: the tab shell under the sticky cockpit header. The tab set
 * is owned by the per-type view (a feature has PRD and no Plan; everything
 * else has Plan and no PRD), so this component only renders and routes it.
 *
 * Each tab's content is a server-rendered slot; only the active one mounts, so
 * a panel's live subscriptions start when its tab opens and stop when it
 * closes. The active tab lives in the URL (`?tab=`) so it is deep-linkable and
 * survives back/forward. */
export function WorkItemTabs({
  tabs,
  type,
  defaultTab,
  slots,
}: {
  tabs: WorkItemTab[];
  /** US-49.4: only the first tab's label depends on it — a feature reads
   * "Feature", a story "Story". The ids are type-independent. */
  type: string;
  defaultTab: WorkItemTab;
  slots: Partial<Record<WorkItemTab, React.ReactNode>>;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [active, setActive] = useState<WorkItemTab>(defaultTab);

  // Follow client-side navigations that address a tab (or a legacy ?panel=),
  // e.g. the PRD review action pushing ?panel=stories. Anything this type
  // doesn't have is ignored, so the body is never blank.
  useEffect(() => {
    const tab = searchParams.get("tab");
    const panel = searchParams.get("panel");
    const asked =
      normalizeTab(tab) ?? (panel ? LEGACY_PANEL_TO_TAB[panel] : null);
    if (asked && tabs.includes(asked)) setActive(asked);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams, tabs.join(",")]);

  // A type change (or a release appearing) can retire the active tab.
  useEffect(() => {
    if (!tabs.includes(active)) setActive(defaultTab);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tabs.join(","), active, defaultTab]);

  function select(tab: WorkItemTab) {
    setActive(tab);
    const params = new URLSearchParams(searchParams.toString());
    params.set("tab", tab);
    params.delete("panel");
    router.replace(`${pathname}?${params.toString()}`, { scroll: false });
  }

  return (
    <div>
      {/* Not sticky: the cockpit header above is what stays pinned (US-15.19),
          and two sticky bars at top-0 would overlap. */}
      <div className="-mx-4 border-b px-4 md:-mx-6 md:px-6">
        <nav
          aria-label="Work item sections"
          className="-mb-px flex gap-1 overflow-x-auto"
        >
          {tabs.map((key) => {
            const Icon = ICONS[key];
            const on = key === active;
            return (
              <button
                key={key}
                type="button"
                onClick={() => select(key)}
                aria-current={on ? "page" : undefined}
                className={cn(
                  "flex shrink-0 items-center gap-1.5 border-b-2 px-3 py-2.5 text-sm transition-colors",
                  on
                    ? "border-foreground font-medium text-foreground"
                    : "border-transparent text-muted-foreground hover:text-foreground"
                )}
              >
                <Icon className="size-4" />
                {tabLabel(key, type)}
              </button>
            );
          })}
        </nav>
      </div>
      <div className="pt-6">{slots[active]}</div>
    </div>
  );
}
