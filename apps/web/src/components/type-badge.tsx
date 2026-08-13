import { Bug, LayoutTemplate, Wrench, BookOpen, type LucideIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

export type IssueType = "feature" | "bug" | "chore" | "story";

/** US-14.10: exported so the create dialog's type picker shows the same
 * icon the badges use everywhere else — a feature should not be a
 * different shape depending on which screen you meet it on. */
export const TYPE_ICONS: Record<IssueType, LucideIcon> = {
  feature: LayoutTemplate,
  bug: Bug,
  chore: Wrench,
  story: BookOpen,
};

const TYPE_STYLES: Record<IssueType, string> = {
  feature:
    "border-indigo-200 bg-indigo-100 text-indigo-700 dark:border-indigo-900 dark:bg-indigo-950 dark:text-indigo-300",
  bug: "border-rose-200 bg-rose-100 text-rose-700 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-300",
  chore:
    "border-slate-200 bg-slate-100 text-slate-700 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300",
  story:
    "border-cyan-200 bg-cyan-100 text-cyan-700 dark:border-cyan-900 dark:bg-cyan-950 dark:text-cyan-300",
};

export function TypeBadge({
  type,
  className,
}: {
  type: IssueType;
  className?: string;
}) {
  const Icon = TYPE_ICONS[type] ?? BookOpen;
  return (
    <Badge
      variant="outline"
      className={cn("capitalize", TYPE_STYLES[type], className)}
    >
      <Icon className="size-3" />
      {type}
    </Badge>
  );
}
