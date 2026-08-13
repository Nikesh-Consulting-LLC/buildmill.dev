import { Suspense } from "react";
import Link from "next/link";
import { redirect } from "next/navigation";
import { AlertTriangle } from "lucide-react";
import { money } from "@/lib/budget";
import { createClient } from "@/lib/supabase/server";
import { resolveActiveOrg } from "@/lib/active-org";
import {
  readGlobalProjectIds,
  resolveGlobalSelection,
} from "@/lib/global-project-selection";
import { GlobalProjectFilter } from "@/components/global-project-filter";
import { PageHeader } from "@/components/page-header";
import { IssueDialog, type EpicOption } from "../issues/issue-dialog";
import { ClarificationsCard } from "./clarifications-card";
import { ParkedRunsCard } from "./parked-runs-card";
import { IncidentsCard } from "./incidents-card";
import { DashboardTabs } from "./dashboard-tabs";
import { loadThingsToDo } from "./data";

export default async function ThingsToDoPage() {
  const supabase = await createClient();

  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const { orgId } = await resolveActiveOrg(supabase, user.id);
  if (!orgId) redirect("/login");

  const {
    groups,
    clarificationItems,
    parkedRuns,
    recommendationItems,
    refreshItems,
    releaseRows,
    agentItems,
    featureRuns,
    deployRows,
    completedItems,
    stalledQueue,
    incidents,
    exhaustedBudgets,
  } = await loadThingsToDo(supabase, orgId);

  // Phase 64: scope every card to the global project filter — replaces the
  // page's own single-project `?project=` chips. The full active-org project
  // list (not the waiting-count chips loadThingsToDo builds) is what the
  // filter and the New work item dialog need — a project with nothing
  // waiting still belongs in both. Scoping this to the active org (rather
  // than every org the caller is a member of) is what keeps the filter,
  // matches()-based card filtering below, and the New work item dialog from
  // leaking projects from a workspace the manager isn't currently in.
  const { data: projectRows } = await supabase
    .from("projects")
    .select("id, name, org_id")
    .eq("org_id", orgId)
    .order("name", { ascending: true });
  const allProjects = projectRows ?? [];

  const { data: epicRows } = await supabase
    .from("epics")
    .select("id, project_id, title, active, status, number")
    .in(
      "project_id",
      allProjects.map((p) => p.id)
    )
    // US-71.1: latest epic first, everywhere epics list.
    .order("number", { ascending: false });
  const epicsByProject: Record<string, EpicOption[]> = {};
  for (const p of allProjects) epicsByProject[p.id] = [];
  for (const e of epicRows ?? []) {
    (epicsByProject[e.project_id] ??= []).push({
      id: e.id,
      title: e.title,
      active: e.active,
      status: e.status,
      number: e.number,
    });
  }

  const storedProjectIds = await readGlobalProjectIds();
  const selectedIds = resolveGlobalSelection(allProjects, storedProjectIds);
  const matches = (projectId: string) => selectedIds.has(projectId);
  const selectedProjects = allProjects.filter((p) => selectedIds.has(p.id));
  const createProject = selectedProjects[0] ?? null;

  const fGroups = groups
    .map((g) => ({ ...g, items: g.items.filter((i) => matches(i.projectId)) }))
    .filter((g) => g.items.length > 0);
  const fClarifications = clarificationItems.filter((c) => matches(c.projectId));
  const fParkedRuns = parkedRuns.filter((r) => matches(r.projectId));
  const fRecommendations = recommendationItems.filter((r) =>
    matches(r.projectId)
  );
  const fRefreshes = refreshItems.filter((r) => matches(r.projectId));
  const fReleases = releaseRows.filter((r) => matches(r.projectId));
  const fAgent = agentItems.filter((a) => matches(a.projectId));
  const fIncidents = incidents.filter((i) => matches(i.projectId));
  const fDeploy = deployRows.filter((d) => matches(d.projectId));
  const fCompleted = completedItems.filter((c) => matches(c.projectId));
  const fWaitingCount =
    fGroups.reduce((n, g) => n + g.items.length, 0) +
    fRecommendations.length +
    fRefreshes.length;
  // US-37.3: respects the project filter like every other collection — a
  // banner about a project the manager has filtered out is noise. It is
  // deliberately absent from fWaitingCount: a budget is a condition, not a
  // work item, and counting it would inflate "N things to do".
  const fBudgets = exhaustedBudgets.filter((b) => matches(b.id));

  return (
    <div className="flex w-full flex-col gap-6">
      <PageHeader
        title="Things to Do"
        description="What the factory is doing, and what it needs from you."
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
          allProjects.length > 0 && (
            <GlobalProjectFilter
              projects={allProjects}
              initialSelected={[...selectedIds]}
            />
          )
        }
      />

      {stalledQueue && (
        <Link
          href="/workers"
          className="flex items-center gap-3 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900 transition-colors hover:bg-amber-100 dark:border-amber-900 dark:bg-amber-950/70 dark:text-amber-200 dark:hover:bg-amber-900/50"
        >
          <AlertTriangle className="size-4 shrink-0" />
          <span className="min-w-0 flex-1">
            <span className="font-semibold">Factory stalled:</span>{" "}
            {stalledQueue.count} item
            {stalledQueue.count > 1 ? "s have" : " has"} been queued for up to{" "}
            {stalledQueue.oldestMinutes}m, but no capable worker is online.
          </span>
          <span className="shrink-0 font-medium underline underline-offset-4">
            Open Workers
          </span>
        </Link>
      )}

      {/* US-37.3: an exhausted budget stops new work from starting. Pinned
          above the tabs with the other alerts, and one banner for every
          project rather than one each — four out-of-budget projects should not
          push the actual work off the screen. */}
      {fBudgets.length > 0 && (
        <div className="flex flex-col gap-2 rounded-lg border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive sm:flex-row sm:items-start sm:gap-3">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" />
          <div className="min-w-0 flex-1">
            <span className="font-semibold">
              {fBudgets.length === 1
                ? "A project is out of budget:"
                : `${fBudgets.length} projects are out of budget:`}
            </span>{" "}
            no new run will start on{" "}
            {fBudgets.length === 1 ? "it" : "them"} until the budget is raised
            or its counter is reset. Runs already going finish normally.
            <ul className="mt-1.5 flex flex-col gap-1">
              {fBudgets.map((b) => (
                <li key={b.id} className="min-w-0">
                  <Link
                    href={`/projects/${b.id}?tab=overview`}
                    className="font-medium underline underline-offset-4"
                  >
                    {b.name}
                  </Link>{" "}
                  <span className="font-mono text-xs tabular-nums">
                    {money(b.spent)} of {money(b.budget)}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}

      {/* US-15.18: collapsed to a glanceable count that expands on demand and
          can be cleared (org-wide acknowledgement). */}
      <IncidentsCard incidents={fIncidents} />

      <ClarificationsCard items={fClarifications} />

      <ParkedRunsCard items={fParkedRuns} />

      {/* US-19.1: one section, four tabs, each a compact table at full width.
          Everything above stays pinned so an alert or an open question is never
          hidden behind a tab. */}
      <Suspense fallback={null}>
        <DashboardTabs
          groups={fGroups}
          recommendations={fRecommendations}
          refreshes={fRefreshes}
          agentItems={fAgent}
          featureRuns={featureRuns}
          completedItems={fCompleted}
          releaseRows={fReleases}
          deployRows={fDeploy}
          waitingCount={fWaitingCount}
          orgId={orgId}
        />
      </Suspense>
    </div>
  );
}
