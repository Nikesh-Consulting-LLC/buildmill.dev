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
import { ReportsHub } from "./reports-hub";
import { REPORT_SELECT, type ReportDeployment, type ReportRow } from "./report-types";
import type { HubProject } from "../issues/issue-view-types";
import type { PromoteEpic } from "./promote-dialog";

/**
 * US-16.6: Reports is cross-project from the start — the same shape Work Items
 * took in Phase 8, rather than starting per-project and consolidating later.
 * The whole point is seeing everything reported at once.
 */
export default async function ReportsPage({
  searchParams,
}: {
  // US-16.7: a promoted work item links back here naming its report, so the
  // link lands on the report rather than on a list the manager has to search.
  searchParams: Promise<{ report?: string }>;
}) {
  const { report: reportParam } = await searchParams;
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

  const REPORTS_TITLE = "Reports";
  const REPORTS_DESCRIPTION =
    "What deployed apps have reported — crashes they caught themselves and issues their users submitted. Promote the real bugs; ignore the rest.";

  if (!projects.length) {
    return (
      <div className="flex w-full flex-col gap-6">
        <PageHeader title={REPORTS_TITLE} description={REPORTS_DESCRIPTION} />
        <EmptyState
          icon={FolderGit2}
          title="No projects yet"
          description="Reports arrive from a project's deployments — create a project and deploy it first."
        />
      </div>
    );
  }

  // Not org-filtered directly, but ReportsHub only ever renders rows whose
  // project_id is in `projects` (now scoped to the active org above).
  const [{ data: reportRows }, { data: deploymentRows }, { data: epicRows }] =
    await Promise.all([
      supabase
        .from("app_issues")
        .select(REPORT_SELECT)
        .order("last_seen_at", { ascending: false })
        .limit(500),
      supabase.from("deployments").select("id, name, project_id, environment"),
      supabase
        .from("epics")
        .select("id, project_id, title, number")
        .eq("status", "open")
        .order("number", { ascending: true }),
    ]);

  const storedProjectIds = await readGlobalProjectIds();
  const selectedIds = [...resolveGlobalSelection(projects, storedProjectIds)];

  return (
    <div className="flex w-full flex-col gap-6">
      <PageHeader
        title={REPORTS_TITLE}
        description={REPORTS_DESCRIPTION}
        filter={<GlobalProjectFilter projects={projects} initialSelected={selectedIds} />}
      />
      <ReportsHub
        projects={projects}
        deployments={(deploymentRows ?? []) as ReportDeployment[]}
        epics={(epicRows ?? []) as PromoteEpic[]}
        initialReports={(reportRows ?? []) as unknown as ReportRow[]}
        initialActiveId={reportParam ?? null}
        selectedIds={selectedIds}
      />
    </div>
  );
}
