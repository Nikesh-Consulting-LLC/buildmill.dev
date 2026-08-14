"use client";

// US-92.1 AC4: the phone's first screen should be work, not warnings.
//
// Above the tabs sit five things — the stalled-queue banner, out-of-budget
// projects, incidents, open questions and parked runs. Each earns its place on
// a desktop, where they cost a strip apiece. Stacked at 375px they push the
// first actual row below two screens of scrolling, on the page a manager opens
// precisely to see whether anything needs them.
//
// Nothing is removed. Below `md` they fold behind one line that counts them,
// which is enough to decide whether to look. At `md`+ this renders its
// children exactly as before.

import { useState } from "react";
import { AlertTriangle, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";

export type PreambleCount = { label: string; n: number };

export function MobilePreamble({
  counts,
  children,
}: {
  /** One entry per kind that has anything to say; zeroes are filtered here. */
  counts: PreambleCount[];
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const live = counts.filter((c) => c.n > 0);

  // Nothing to warn about: no strip, and no empty container either.
  if (!live.length) return null;

  const summary = live
    .map((c) => `${c.n} ${c.label}${c.n === 1 ? "" : "s"}`)
    .join(" · ");

  return (
    <>
      <div className="md:hidden">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="flex w-full items-center gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2.5 text-left text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/70 dark:text-amber-200"
        >
          <AlertTriangle className="size-4 shrink-0" />
          <span className="min-w-0 flex-1 truncate font-medium">{summary}</span>
          <ChevronRight
            className={cn(
              "size-4 shrink-0 transition-transform",
              open && "rotate-90"
            )}
          />
        </button>
        {open && <div className="mt-2 grid gap-3">{children}</div>}
      </div>
      <div className="hidden md:contents">{children}</div>
    </>
  );
}
