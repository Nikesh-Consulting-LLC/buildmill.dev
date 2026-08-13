"use client";

import { useState } from "react";
import Link from "next/link";
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
} from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

export type SetupCheck = {
  label: string;
  detail: string;
  done: boolean;
  href: string;
};

/** US-7.7: the project-setup readiness panel — an honest at-a-glance signal of
 * whether an agent has everything it needs to build. Advisory only: nothing is
 * gated on it. Each check deep-links to the tab that resolves it. Once every
 * check passes the panel collapses to a single "Ready" line; it stays open (and
 * can't be usefully collapsed) while setup is incomplete. */
export function ProjectSetupReadinessCard({ checks }: { checks: SetupCheck[] }) {
  const doneCount = checks.filter((c) => c.done).length;
  const allDone = doneCount === checks.length;
  // Collapse once complete; keep it open while there's still work to do.
  const [expanded, setExpanded] = useState(!allDone);

  const collapsible = allDone;

  return (
    <Card>
      <CardHeader
        className={
          "flex flex-row items-center justify-between gap-3 space-y-0" +
          (collapsible ? " cursor-pointer" : "")
        }
        onClick={collapsible ? () => setExpanded((e) => !e) : undefined}
      >
        <div className="flex min-w-0 items-start gap-2">
          {collapsible &&
            (expanded ? (
              <ChevronDown className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
            ) : (
              <ChevronRight className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
            ))}
          <div className="space-y-1.5">
            <CardTitle className="text-base">Project setup</CardTitle>
            {expanded && (
              <CardDescription>
                What this project needs so an agent can build it. Advisory —
                nothing is blocked while it&apos;s incomplete.
              </CardDescription>
            )}
          </div>
        </div>
        {allDone ? (
          <Badge className="gap-1 border-emerald-200 bg-emerald-100 font-normal text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-300">
            <CheckCircle2 className="size-3.5" />
            Ready
          </Badge>
        ) : (
          <Badge variant="secondary" className="font-normal">
            {doneCount} of {checks.length} complete
          </Badge>
        )}
      </CardHeader>
      {expanded && (
        <CardContent>
          <ul className="grid gap-1.5">
            {checks.map((c) => (
              <li key={c.label}>
                <Link
                  href={c.href}
                  className="flex items-start gap-3 rounded-md border px-3 py-2 text-sm transition-colors hover:border-ring/60"
                >
                  {c.done ? (
                    <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-600" />
                  ) : (
                    <Circle className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                  )}
                  <span className="min-w-0">
                    <span className="font-medium">{c.label}</span>
                    <span className="ml-2 text-xs text-muted-foreground">
                      {c.done ? "Done" : "Not done"}
                    </span>
                    <span className="block text-xs text-muted-foreground">
                      {c.detail}
                    </span>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </CardContent>
      )}
    </Card>
  );
}
