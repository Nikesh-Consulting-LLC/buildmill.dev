import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { ArrowLeft, Layers } from "lucide-react";
import { createClient } from "@/lib/supabase/server";
import {
  formatEpicProgress,
  formatEpicTypeCounts,
  rollupEpicIssues,
  type EpicMemberIssue,
  type EpicRollup,
  type EpicRow,
} from "@/lib/epics";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/empty-state";
import { EpicDialog } from "../epic-dialog";

/** Epics list for a project (us-2.8): every epic with a one-line progress
 * summary and a percent bar, rolled up server-side from its members. */
export default async function EpicsListPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const supabase = await createClient();

  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const { data: project } = await supabase
    .from("projects")
    .select("id, org_id, name")
    .eq("id", id)
    .maybeSingle();
  if (!project) notFound();

  const { data: epics } = await supabase
    .from("epics")
    .select("id, project_id, title, description, status, created_at, updated_at")
    .eq("project_id", project.id)
    .order("created_at", { ascending: false });

  const { data: epicMembers } = await supabase
    .from("issues")
    .select("id, title, type, status, parent_id, abandoned_at, epic_id")
    .eq("project_id", project.id)
    .not("epic_id", "is", null);

  const rollups: Record<string, EpicRollup> = {};
  for (const epic of epics ?? []) {
    rollups[epic.id] = rollupEpicIssues(
      (epicMembers ?? []).filter((m) => m.epic_id === epic.id) as EpicMemberIssue[]
    );
  }

  return (
    <div className="flex w-full flex-col gap-6">
      <div className="flex items-center justify-between gap-4">
        <div className="min-w-0">
          <Link
            href={`/projects/${project.id}`}
            className="mb-1 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="size-3" />
            {project.name}
          </Link>
          <h1 className="text-2xl font-semibold tracking-tight">Epics</h1>
        </div>
        <EpicDialog orgId={project.org_id} projectId={project.id} />
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">All epics</CardTitle>
          <CardDescription>
            Larger initiatives — related issues group under an epic and roll
            up to one progress view.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {!epics?.length ? (
            <EmptyState
              icon={Layers}
              title="No epics yet"
              description="Group related work items under an initiative to track it as a whole."
            />
          ) : (
            <ul className="grid gap-1.5">
              {(epics as EpicRow[]).map((epic) => {
                const rollup = rollups[epic.id];
                const counts = formatEpicTypeCounts(rollup);
                return (
                  <li key={epic.id}>
                    <Link
                      href={`/projects/${project.id}/epics/${epic.id}`}
                      className="flex items-center justify-between gap-3 rounded-md border px-3 py-2 text-sm transition-colors hover:border-ring/60"
                    >
                      <span className="flex min-w-0 flex-col gap-0.5">
                        <span className="truncate font-medium">{epic.title}</span>
                        <span className="truncate text-xs text-muted-foreground">
                          {formatEpicProgress(rollup)}
                          {counts && ` · ${counts}`}
                        </span>
                      </span>
                      <span className="flex shrink-0 items-center gap-2">
                        {epic.status === "completed" ? (
                          <Badge variant="secondary">Completed</Badge>
                        ) : rollup.readyToComplete ? (
                          <Badge variant="secondary">Ready to complete</Badge>
                        ) : null}
                        <span className="hidden h-1.5 w-24 overflow-hidden rounded-full bg-muted sm:block">
                          <span
                            className="block h-full rounded-full bg-emerald-500 transition-[width]"
                            style={{ width: `${rollup.percent}%` }}
                          />
                        </span>
                        <span className="w-9 shrink-0 text-right font-mono text-xs text-muted-foreground">
                          {rollup.percent}%
                        </span>
                      </span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
