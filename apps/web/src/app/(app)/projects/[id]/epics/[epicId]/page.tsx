import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { ArrowLeft, CircleCheck, ListTodo } from "lucide-react";
import { MarkdownView } from "@/components/markdown-view";
import { createClient } from "@/lib/supabase/server";
import {
  formatEpicProgress,
  formatEpicTypeCounts,
  groupEpicIssues,
  rollupEpicIssues,
  type EpicMemberIssue,
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
import { StatusBadge, type IssueStatus } from "@/components/status-badge";
import { EpicDialog } from "../../epic-dialog";
import { EpicActions } from "../../epic-actions";

const TYPE_BADGE_LABEL: Record<string, string> = {
  feature: "Feature",
  bug: "Bug",
  chore: "Chore",
  story: "Story",
};

function IssueLine({
  issue,
  backTo,
}: {
  issue: EpicMemberIssue;
  backTo: string;
}) {
  return (
    <Link
      href={`/issues/${issue.id}?${backTo}`}
      className="flex items-center justify-between gap-3 rounded-md border px-3 py-2 text-sm transition-colors hover:border-ring/60"
    >
      <span className="flex min-w-0 items-center gap-2">
        <Badge variant="secondary" className="shrink-0 font-normal">
          {TYPE_BADGE_LABEL[issue.type] ?? issue.type}
        </Badge>
        <span className="truncate font-medium">{issue.title}</span>
      </span>
      <span className="flex shrink-0 items-center gap-2">
        {issue.abandoned_at && <Badge variant="secondary">Abandoned</Badge>}
        <StatusBadge status={issue.status as IssueStatus} />
      </span>
    </Link>
  );
}

export default async function EpicDetailPage({
  params,
}: {
  params: Promise<{ id: string; epicId: string }>;
}) {
  const { id, epicId } = await params;
  const supabase = await createClient();

  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const { data: epic } = await supabase
    .from("epics")
    // FK named: `issues` holds NOT NULL foreign keys to both epics and
    // projects, so PostgREST reads it as a junction and an un-hinted embed is
    // ambiguous (300, PGRST201) — which made this page 404. See BUG-1.1.
    .select(
      "id, org_id, project_id, title, description, status, active, created_at, updated_at, projects!epics_project_id_org_id_fkey(name)"
    )
    .eq("id", epicId)
    .eq("project_id", id)
    .maybeSingle();

  if (!epic) notFound();

  const projectName =
    (epic.projects as unknown as { name: string } | null)?.name ?? "Project";

  const { data: memberRows } = await supabase
    .from("issues")
    .select("id, title, type, status, parent_id, abandoned_at")
    .eq("epic_id", epic.id)
    .order("created_at", { ascending: true });

  const members = (memberRows ?? []) as EpicMemberIssue[];
  const rollup = rollupEpicIssues(members);
  const { features, others } = groupEpicIssues(members);
  const typeCounts = formatEpicTypeCounts(rollup);
  const backTo = `from=${encodeURIComponent(`/projects/${id}/epics/${epicId}`)}&fromLabel=${encodeURIComponent(epic.title)}`;

  return (
    <div className="flex w-full flex-col gap-6">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <Link
            href={`/projects/${epic.project_id}/epics`}
            className="mb-1 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="size-3" />
            {projectName} · Epics
          </Link>
          <div className="flex items-center gap-2">
            <h1 className="truncate text-2xl font-semibold tracking-tight">
              {epic.title}
            </h1>
            {epic.status === "completed" && (
              <Badge variant="secondary">Completed</Badge>
            )}
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-2">
          <div className="flex items-center gap-2">
            <EpicDialog
              orgId={epic.org_id}
              projectId={epic.project_id}
              epic={{
                id: epic.id,
                title: epic.title,
                description: epic.description,
              }}
            />
          </div>
          <EpicActions
            orgId={epic.org_id}
            epicId={epic.id}
            projectId={epic.project_id}
            title={epic.title}
            status={epic.status as "open" | "completed"}
            active={epic.active}
            memberCount={members.length}
            redirectOnDelete
          />
        </div>
      </div>

      {epic.status === "open" && rollup.readyToComplete && (
        <div className="flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-300">
          <CircleCheck className="size-4 shrink-0" />
          Every issue in this epic is done — ready to complete it?
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Progress</CardTitle>
          <CardDescription>
            {formatEpicProgress(rollup)}
            {typeCounts && ` · ${typeCounts}`}
            {rollup.abandoned > 0 &&
              ` · ${rollup.abandoned} abandoned (not counted)`}
          </CardDescription>
        </CardHeader>
        <CardContent className="flex items-center gap-3">
          <span className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
            <span
              className="block h-full rounded-full bg-emerald-500 transition-[width]"
              style={{ width: `${rollup.percent}%` }}
            />
          </span>
          <span className="shrink-0 font-mono text-sm text-muted-foreground">
            {rollup.percent}%
          </span>
        </CardContent>
      </Card>

      {epic.description && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Description</CardTitle>
          </CardHeader>
          <CardContent>
            <MarkdownView>{epic.description}</MarkdownView>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Work Items</CardTitle>
          <CardDescription>
            Features with their child stories, then bugs and chores.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {!members.length ? (
            <EmptyState
              icon={ListTodo}
              title="No work items in this epic"
              description="Assign a work item to this epic from the work item's edit dialog."
            />
          ) : (
            <div className="grid gap-3">
              {features.map(({ feature, children }) => (
                <div key={feature.id} className="grid gap-1.5">
                  <IssueLine issue={feature} backTo={backTo} />
                  {children.length > 0 && (
                    <div className="ml-4 grid gap-1.5 border-l pl-3">
                      {children.map((c) => (
                        <IssueLine key={c.id} issue={c} backTo={backTo} />
                      ))}
                    </div>
                  )}
                </div>
              ))}
              {others.map((issue) => (
                <IssueLine key={issue.id} issue={issue} backTo={backTo} />
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
