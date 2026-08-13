"use client";

import { useEffect, useState, useTransition } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Search, X } from "lucide-react";
import { Input } from "@/components/ui/input";

/** Debounced search box that writes `?q=` into the URL (US-2.11). Search
 * spans the hub's project selection (US-8.1); only the `?project` deep-link
 * seed and the active/abandoned view are preserved in the URL. */
export function IssueSearchInput({
  seededProjectId,
  abandoned,
  initialQuery,
}: {
  seededProjectId: string | null;
  abandoned: boolean;
  initialQuery: string;
}) {
  const router = useRouter();
  const [value, setValue] = useState(initialQuery);
  const [, startTransition] = useTransition();

  useEffect(() => {
    setValue(initialQuery);
  }, [initialQuery]);

  useEffect(() => {
    const handle = window.setTimeout(() => {
      const trimmed = value.trim();
      if (trimmed === initialQuery.trim()) return;

      const params = new URLSearchParams();
      if (seededProjectId) params.set("project", seededProjectId);
      if (abandoned) params.set("view", "abandoned");
      if (trimmed) params.set("q", trimmed);

      const s = params.toString();
      startTransition(() => {
        router.replace(s ? `/issues?${s}` : "/issues");
      });
    }, 300);
    return () => window.clearTimeout(handle);
  }, [value, initialQuery, seededProjectId, abandoned, router]);

  return (
    <div className="relative w-full max-w-sm">
      <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
      <Input
        type="search"
        placeholder="Search title, body, criteria, #…"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        className="h-9 pr-8 pl-8"
        aria-label="Search work items"
      />
      {value && (
        <button
          type="button"
          className="absolute top-1/2 right-2 -translate-y-1/2 rounded p-0.5 text-muted-foreground hover:text-foreground"
          aria-label="Clear search"
          onClick={() => setValue("")}
        >
          <X className="size-3.5" />
        </button>
      )}
    </div>
  );
}
