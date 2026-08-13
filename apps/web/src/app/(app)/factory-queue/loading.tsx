// US-87.11: shown while this route's server work is in flight. Wrapped in
// `<ViewTransition exit="skeleton-out">` so it hands off to the real content
// instead of vanishing under it — see globals.css for the paired
// `::view-transition-old/new` rules.
import { ViewTransition } from "react";
import { PageHeaderSkeleton, ToolbarSkeleton, RowsSkeleton } from "@/components/page-skeleton";

export default function Loading() {
  return (
    <ViewTransition exit="skeleton-out">
      <div className="flex w-full flex-col gap-6">
        <PageHeaderSkeleton />
        <ToolbarSkeleton />
        <RowsSkeleton rows={8} />
      </div>
    </ViewTransition>
  );
}
