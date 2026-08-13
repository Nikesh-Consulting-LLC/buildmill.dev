import { Skeleton } from "@/components/ui/skeleton";

/**
 * US-87.11: the shapes a `loading.tsx` renders while a route's server work is
 * in flight. There were no loading states anywhere in the app before this, so
 * navigation showed the previous page until the new one was ready and then
 * swapped abruptly.
 *
 * These deliberately mirror the real layouts they stand in for — the same
 * `PageHeader` proportions, the same toolbar row, the same row rhythm — so the
 * handoff into content moves as little as possible. A skeleton that does not
 * match its page is worse than none: it makes the arrival of real content look
 * like a second navigation.
 *
 * Composed from the shared `Skeleton` primitive rather than a new one.
 */

/** Matches `PageHeader`: title + description left, actions/filter right. */
export function PageHeaderSkeleton({ actions = true }: { actions?: boolean }) {
  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-start sm:justify-between sm:gap-4">
      <div className="min-w-0 sm:flex-1">
        <Skeleton className="h-7 w-48 sm:h-8" />
        <Skeleton className="mt-2 hidden h-4 w-80 max-w-full sm:block" />
      </div>
      {actions && (
        <div className="flex flex-wrap items-center gap-3 sm:shrink-0 sm:justify-end">
          <Skeleton className="h-9 w-32" />
          <Skeleton className="h-9 w-40" />
        </div>
      )}
    </div>
  );
}

/** The search + filters row the hub and the queue both carry. */
export function ToolbarSkeleton() {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <Skeleton className="h-9 w-full max-w-sm" />
      <div className="flex items-center gap-1.5">
        <Skeleton className="h-8 w-20" />
        <Skeleton className="h-8 w-24" />
      </div>
    </div>
  );
}

/** A bordered list of rows — the Outline/Table/queue shape. */
export function RowsSkeleton({ rows = 8 }: { rows?: number }) {
  return (
    <div className="overflow-hidden rounded-lg border">
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className="flex items-center gap-3 border-b px-3 py-2.5 last:border-0"
        >
          <Skeleton className="size-4 shrink-0 rounded" />
          <Skeleton className="h-4 w-16 shrink-0" />
          {/* Varying widths so it reads as a list of different things rather
              than a loading pattern. */}
          <Skeleton
            className="h-4 min-w-24 flex-1"
            style={{ maxWidth: `${55 + ((i * 37) % 40)}%` }}
          />
          <Skeleton className="h-5 w-20 shrink-0 rounded-full" />
        </div>
      ))}
    </div>
  );
}

/** Stacked cards — Things to Do, and any card-per-concern page. */
export function CardsSkeleton({ cards = 3 }: { cards?: number }) {
  return (
    <div className="flex flex-col gap-4">
      {Array.from({ length: cards }).map((_, i) => (
        <div key={i} className="flex flex-col gap-3 rounded-lg border p-4">
          <Skeleton className="h-5 w-40" />
          <Skeleton className="h-4 w-full max-w-2xl" />
          <Skeleton className="h-4 w-2/3 max-w-xl" />
        </div>
      ))}
    </div>
  );
}

/** A detail page: header, meta strip, then a body column beside a sidebar. */
export function DetailSkeleton() {
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-3">
        <Skeleton className="h-4 w-32" />
        <Skeleton className="h-8 w-2/3 max-w-xl" />
        <div className="flex flex-wrap gap-2">
          <Skeleton className="h-6 w-24 rounded-full" />
          <Skeleton className="h-6 w-20 rounded-full" />
          <Skeleton className="h-6 w-28 rounded-full" />
        </div>
      </div>
      <div className="flex flex-col gap-6 lg:flex-row">
        <div className="flex min-w-0 flex-1 flex-col gap-3">
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-4/5" />
          <Skeleton className="mt-3 h-40 w-full rounded-lg" />
        </div>
        <div className="flex w-full shrink-0 flex-col gap-3 lg:w-72">
          <Skeleton className="h-24 w-full rounded-lg" />
          <Skeleton className="h-32 w-full rounded-lg" />
        </div>
      </div>
    </div>
  );
}
