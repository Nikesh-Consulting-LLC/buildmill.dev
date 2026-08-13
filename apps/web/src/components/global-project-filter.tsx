"use client";

import { useState, useTransition } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";
import { projectColor } from "@/lib/work-items";
import { setGlobalProjects } from "@/lib/global-project-filter-action";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

/**
 * The one project filter for the whole app — replaces the per-page pickers
 * Work Items, Reports, Things to Do and Testing each used to keep
 * separately. Lives once in the app shell; every page resolves its own data
 * against the same cookie-backed selection, server-side, so there is no
 * flash of unfiltered content and no page-to-page drift.
 */
export function GlobalProjectFilter({
  projects,
  initialSelected,
}: {
  projects: { id: string; name: string }[];
  initialSelected: string[];
}) {
  const [selected, setSelected] = useState<Set<string>>(
    () => new Set(initialSelected)
  );
  const [pending, startTransition] = useTransition();
  const router = useRouter();

  const allSelected = selected.size === projects.length;

  function commit(next: Set<string>) {
    setSelected(next);
    startTransition(async () => {
      const isAll = next.size === projects.length;
      await setGlobalProjects(isAll ? null : [...next]);
      router.refresh();
    });
  }

  function toggle(id: string) {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    commit(next);
  }

  const label = allSelected
    ? `All projects (${projects.length})`
    : selected.size === 0
      ? "No projects"
      : selected.size === 1
        ? (projects.find((p) => selected.has(p.id))?.name ?? "1 project")
        : `${selected.size} projects`;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className={cn(
          "flex items-center gap-2 rounded-full border border-input px-3 py-1.5 text-sm font-medium text-foreground transition-colors hover:bg-muted",
          "data-[popup-open]:bg-muted",
          pending && "opacity-70"
        )}
      >
        <span className="flex -space-x-1">
          {(selected.size ? projects.filter((p) => selected.has(p.id)) : projects)
            .slice(0, 4)
            .map((p) => (
              <span
                key={p.id}
                className="size-2.5 rounded-full ring-1 ring-background"
                style={{ backgroundColor: projectColor(p.id) }}
              />
            ))}
        </span>
        {label}
        <ChevronDown className="size-3.5 text-muted-foreground" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-64">
        <div className="flex items-center justify-between px-1.5 py-1 text-xs text-muted-foreground">
          <span>Projects</span>
          <span className="flex gap-2">
            <button
              type="button"
              className="hover:text-foreground"
              onClick={() => commit(new Set(projects.map((p) => p.id)))}
            >
              Select all
            </button>
            <button
              type="button"
              className="hover:text-foreground"
              onClick={() => commit(new Set())}
            >
              Clear
            </button>
          </span>
        </div>
        <DropdownMenuSeparator />
        <div className="max-h-72 overflow-y-auto">
          {projects.map((p) => (
            <DropdownMenuCheckboxItem
              key={p.id}
              checked={selected.has(p.id)}
              onCheckedChange={() => toggle(p.id)}
            >
              <span
                className="size-2.5 shrink-0 rounded-full"
                style={{ backgroundColor: projectColor(p.id) }}
              />
              <span className="truncate">{p.name}</span>
            </DropdownMenuCheckboxItem>
          ))}
          {projects.length === 0 && (
            <DropdownMenuItem disabled>No projects</DropdownMenuItem>
          )}
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
