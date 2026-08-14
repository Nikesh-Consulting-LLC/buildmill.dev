"use client";

// US-92.2: one filter button, not eleven pills.
//
// Measured at 375px, Work Items opened with eleven controls before a single
// work item: the project filter, `All types`, four type pills, the status set,
// and three lens switches. They wrapped to four rows and pushed the list below
// the fold — on the page whose entire job is showing the list.
//
// A phone is not a small desktop. Filters are something you open, adjust and
// close; they are not furniture.

import { useState } from "react";
import { SlidersHorizontal } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { StatusBadge, type IssueStatus } from "@/components/status-badge";
import { TYPE_LABELS, ISSUE_TYPES } from "@/lib/issue-body";
import type { IssueType } from "@/components/type-badge";
import { cn } from "@/lib/utils";
import { defaultStatusSelection } from "@/lib/issue-status-filter";
import { ISSUE_STATUS_ORDER } from "./issue-view-types";

export function MobileFilters({
  typeFilter,
  onTypeChange,
  statusFilter,
  onStatusChange,
}: {
  typeFilter: IssueType | null;
  onTypeChange: (v: IssueType | null) => void;
  statusFilter: ReadonlySet<string>;
  onStatusChange: (v: ReadonlySet<string>) => void;
}) {
  const [open, setOpen] = useState(false);

  // AC1: the trigger must never be silent about a narrowed list — the same
  // rule us-91.5's pill follows. Type counts as one; status counts as one
  // whenever it is not "everything".
  const statusNarrowed = statusFilter.size !== ISSUE_STATUS_ORDER.length;
  const activeCount = (typeFilter ? 1 : 0) + (statusNarrowed ? 1 : 0);

  function toggleStatus(s: string) {
    const next = new Set(statusFilter);
    if (next.has(s)) next.delete(s);
    else next.add(s);
    onStatusChange(next);
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button variant="outline" size="sm" className="h-9" />}>
        <SlidersHorizontal className="size-4" />
        Filters
        {activeCount > 0 && (
          <span className="rounded-full bg-primary px-1.5 text-[10.5px] font-semibold tabular-nums text-primary-foreground">
            {activeCount}
          </span>
        )}
      </DialogTrigger>
      <DialogContent className="max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Filters</DialogTitle>
        </DialogHeader>

        <div className="grid gap-5">
          <section className="grid gap-2">
            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Type
            </p>
            <div className="flex flex-wrap gap-1.5">
              <FilterChip
                active={!typeFilter}
                onClick={() => onTypeChange(null)}
                label="All types"
              />
              {ISSUE_TYPES.map((t) => (
                <FilterChip
                  key={t}
                  active={typeFilter === t}
                  onClick={() => onTypeChange(typeFilter === t ? null : t)}
                  label={TYPE_LABELS[t]}
                />
              ))}
            </div>
          </section>

          <section className="grid gap-2">
            <div className="flex items-center justify-between">
              <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Status
              </p>
              <div className="flex items-center gap-3 text-xs text-muted-foreground">
                <button
                  type="button"
                  className="hover:text-foreground"
                  onClick={() => onStatusChange(new Set(ISSUE_STATUS_ORDER))}
                >
                  Select all
                </button>
                <button
                  type="button"
                  className="hover:text-foreground"
                  onClick={() =>
                    onStatusChange(defaultStatusSelection(ISSUE_STATUS_ORDER))
                  }
                >
                  Reset
                </button>
              </div>
            </div>
            <div className="grid gap-1">
              {ISSUE_STATUS_ORDER.map((s) => (
                <label
                  key={s}
                  className="flex min-h-10 cursor-pointer items-center gap-3 rounded-md px-1"
                >
                  <input
                    type="checkbox"
                    className="size-4"
                    checked={statusFilter.has(s)}
                    onChange={() => toggleStatus(s)}
                  />
                  <StatusBadge status={s as IssueStatus} />
                </label>
              ))}
            </div>
          </section>
        </div>

        <DialogClose
          render={<Button className="h-11 w-full" />}
          onClick={() => setOpen(false)}
        >
          Show results
        </DialogClose>
      </DialogContent>
    </Dialog>
  );
}

function FilterChip({
  active,
  onClick,
  label,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "min-h-10 rounded-full border px-3 text-sm font-medium transition-colors",
        active
          ? "border-primary bg-primary text-primary-foreground"
          : "border-input text-muted-foreground"
      )}
    >
      {label}
    </button>
  );
}
