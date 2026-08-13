import { redirect } from "next/navigation";
import { FolderGit2 } from "lucide-react";
import { createClient } from "@/lib/supabase/server";
import { resolveActiveOrg } from "@/lib/active-org";
import {
  readGlobalProjectIds,
  resolveGlobalSelection,
} from "@/lib/global-project-selection";
import { EmptyState } from "@/components/empty-state";
import { GlobalProjectFilter } from "@/components/global-project-filter";
import { PageHeader } from "@/components/page-header";
import { IssueDialog, type EpicOption } from "./issue-dialog";
import { IssuesHub } from "./issues-hub";
import { hubIssuesQuery, hubAbandonedQuery } from "./hub-query";
import {
  HUB_PAGE_SIZE,
  mapIssueRow,
  type HubEpic,
  type HubProject,
  type ViewIssue,
} from "./issue-view-types";

export default async function IssuesPage({
  searchParams,
}: {
  searchParams: Promise<{ project?: string; view?: string; q?: string }>;
}) {
  const { project: projectParam, view, q } = await searchParams;
  const showAbandoned = view === "abandoned";
  const searchQuery = q?.trim() ?? "";
  const supabase = await createClient();

  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const { orgId } = await resolveActiveOrg(supabase, user.id);
  if (!orgId) redirect("/login");

  const { data: projectRows } = await supabase
    .from("projects")
    .select("id, name, org_id")
    .eq("org_id", orgId)
    .order("created_at", { ascending: true });
  const projects = (projectRows ?? []) as HubProject[];

  if (!projects.length) {
    return (
      <div className="flex w-full flex-col gap-6">
        <PageHeader
          title="Work Items"
          description="Define and drive work across every project — outline, board, or table."
        />
        <EmptyState
          icon={FolderGit2}
          title="No projects yet"
          description="Create a project first — work items live inside projects."
        />
      </div>
    );
  }

  const projectIds = projects.map((p) => p.id);
  // US-8.1: the deep-link seed only counts when it names a real project.
  const seededProjectId =
    projectParam && projectIds.includes(projectParam) ? projectParam : null;

  // Phase 64: the global project filter is the source of truth for which
  // projects are in view; a deep-link seed narrows it further for this load
  // (see the effect in IssuesHub that persists the seed into the cookie).
  const storedProjectIds = await readGlobalProjectIds();
  const globalSelectedIds = [...resolveGlobalSelection(projects, storedProjectIds)];
  const selectedIds = seededProjectId ? [seededProjectId] : globalSelectedIds;

  // US-87.3: the hub used to load EVERY project in the org — with each item's
  // full markdown body — and narrow to the selection in the browser. It now
  // asks for the projects actually in view, one bounded page at a time. The
  // global project filter calls `router.refresh()` after it writes its
  // cookie, so changing the selection re-runs this query rather than
  // re-slicing something already in memory.
  const [
    { data: activeRows, error: activeError, count: activeCount },
    { data: abandonedRows, error: abandonedError, count: abandonedCount },
    { data: epicRows },
  ] = await Promise.all([
    hubIssuesQuery(supabase, {
      projectIds: selectedIds,
      search: showAbandoned ? "" : searchQuery,
      from: 0,
      to: HUB_PAGE_SIZE - 1,
      withCount: true,
    }),
    hubAbandonedQuery(supabase, {
      projectIds: selectedIds,
      search: showAbandoned ? searchQuery : "",
      from: 0,
      to: HUB_PAGE_SIZE - 1,
      withCount: true,
    }),
    supabase
      .from("epics")
      .select("id, project_id, number, title, active, status")
      .in("project_id", projectIds)
      // US-71.1: latest epic first, everywhere epics list.
      .order("number", { ascending: false }),
  ]);
  // A failed query must not render as an empty project (this hid a missing
  // migration once) — surface it instead.
  const loadError = activeError ?? abandonedError;

  const activeIssues: ViewIssue[] = (activeRows ?? []).map((row) =>
    mapIssueRow(row as Record<string, unknown>)
  );
  const abandonedIssues = (abandonedRows ?? []) as {
    id: string;
    title: string;
    status: string;
    updated_at: string;
    project_id: string;
  }[];
  const epics = (epicRows ?? []) as HubEpic[];

  // Phase 64: New work item lives in the page header now, next to the
  // filter, like every other page's create action — computed the same way
  // IssuesHub used to compute it client-side, just server-side here.
  const epicsByProject: Record<string, EpicOption[]> = {};
  for (const p of projects) epicsByProject[p.id] = [];
  for (const e of epics) {
    // US-71.1: `number` and `status` must flow — without `status` the picker
    // could not drop closed epics (the US-20.3 filter silently passed
    // everything from this call site).
    (epicsByProject[e.project_id] ??= []).push({
      id: e.id,
      title: e.title,
      active: e.active,
      status: e.status,
      number: e.number,
    });
  }
  const selectedProjects = projects.filter((p) => selectedIds.includes(p.id));
  const createProject = selectedProjects[0] ?? null;

  return (
    <div className="flex w-full flex-col gap-6">
      <PageHeader
        title="Work Items"
        description="Define and drive work across every project — updating live."
        actions={
          createProject && (
            <IssueDialog
              orgId={createProject.org_id}
              projectId={createProject.id}
              epics={epicsByProject[createProject.id]}
              projects={
                selectedProjects.length > 1 ? selectedProjects : undefined
              }
              epicsByProject={epicsByProject}
            />
          )
        }
        filter={
          <GlobalProjectFilter projects={projects} initialSelected={globalSelectedIds} />
        }
      />

      {loadError && (
        <p className="rounded-lg border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          Work items could not be loaded: {loadError.message}
        </p>
      )}

      <IssuesHub
        projects={projects}
        epics={epics}
        activeIssues={activeIssues}
        abandonedIssues={abandonedIssues}
        selectedIds={selectedIds}
        seededProjectId={seededProjectId}
        searchQuery={searchQuery}
        showAbandoned={showAbandoned}
        totalActive={activeCount ?? activeIssues.length}
        totalAbandoned={abandonedCount ?? abandonedIssues.length}
      />
    </div>
  );
}
