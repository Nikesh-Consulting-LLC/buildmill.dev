"use client";

// US-91.18: merged work asks to be released.
//
// Between "merged" and "live" the dashboard said nothing — work sat in the
// default branch until the manager remembered to cut. This is the prompt,
// placed where the work already is.
//
// The claim here is the cheap one (items whose status moved to `merged` since
// the last release that SHIPPED). What a cut would actually contain is the
// commit range, and only the preview endpoint knows it — so the button opens
// the real dialog and the card says which of the two to believe.

import Link from "next/link";
import { Tag } from "lucide-react";

import { TypeBadge, type IssueType } from "@/components/type-badge";
import { CutReleaseDialog } from "../releases/cut-release-dialog";
import type { ReleaseSuggestion } from "./data";

export function ReleaseSuggestions({
  suggestions,
}: {
  suggestions: ReleaseSuggestion[];
}) {
  // AC6: nothing to release, no card. It never becomes furniture.
  if (!suggestions.length) return null;

  return (
    <section className="grid gap-2">
      {suggestions.map((s) => (
        <div
          key={s.projectId}
          className="min-w-0 rounded-lg border border-emerald-300/70 bg-emerald-50/50 p-3 dark:border-emerald-900 dark:bg-emerald-950/20"
        >
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div className="min-w-0">
              <p className="text-sm font-medium">
                {s.capped ? `${s.total}+` : s.total} work item
                {s.total === 1 ? "" : "s"} merged
                {s.sinceVersion ? (
                  <>
                    {" "}
                    since{" "}
                    <span className="font-mono tabular-nums">
                      {s.sinceVersion}
                    </span>
                  </>
                ) : (
                  " and never released"
                )}
              </p>
              <p className="text-xs text-muted-foreground">
                {s.project}
                {" · "}
                {/* AC4: two numbers must not argue. The dialog runs the real
                    preview against the commit range; this is a prompt. */}
                <span title="Counted from work items marked merged. The cut dialog runs the real preview against the commit range and is the authority on what a release would contain.">
                  ready to release
                </span>
              </p>
            </div>

            {s.blocker ? (
              // AC5: an invitation that fails on click is worse than none.
              <span className="shrink-0 rounded-full border border-amber-300 px-2.5 py-1 text-xs text-amber-700 dark:border-amber-900 dark:text-amber-400">
                {s.blocker}
              </span>
            ) : (
              <CutReleaseDialog
                projects={[{ id: s.projectId, name: s.project }]}
                defaultProjectId={s.projectId}
                trigger={
                  <span className="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-md bg-create px-3 text-sm font-medium text-create-foreground transition-colors hover:bg-create/85">
                    <Tag className="size-4" />
                    Cut release
                  </span>
                }
              />
            )}
          </div>

          {/* AC2: name the items. A bare count cannot be checked, and the
              manager is being asked to ship these. */}
          <ul className="mt-2 grid gap-1 border-t pt-2">
            {s.items.map((i) => (
              <li key={i.id} className="flex min-w-0 items-center gap-2 text-xs">
                <TypeBadge type={i.type as IssueType} />
                {i.displayId && (
                  <span className="shrink-0 font-mono text-muted-foreground">
                    {i.displayId}
                  </span>
                )}
                <Link
                  href={`/issues/${i.id}?from=dashboard`}
                  className="min-w-0 truncate hover:underline"
                >
                  {i.title}
                </Link>
              </li>
            ))}
            {s.total > s.items.length && (
              <li className="text-xs text-muted-foreground">
                + {s.total - s.items.length} more
              </li>
            )}
          </ul>
        </div>
      ))}
    </section>
  );
}
