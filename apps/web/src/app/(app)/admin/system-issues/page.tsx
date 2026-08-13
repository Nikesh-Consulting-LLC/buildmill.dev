import { createClient } from "@/lib/supabase/server";
import { SystemIssuesConsole } from "./system-issues-console";
import { REPORT_SELECT, type ReportRow } from "../../reports/report-types";

/**
 * US-16.9: what Build Mill itself has reported, across every org.
 *
 * The admin layout already gates every /admin route on is_platform_admin();
 * the read is narrowed a second time in the database — the platform-admin
 * policy only covers rows whose deployment is flagged self-monitoring, so this
 * console is not a way around the org boundary US-16.1 draws.
 */
export default async function SystemIssuesPage() {
  const supabase = await createClient();

  const { data: selfDeployments } = await supabase
    .from("deployments")
    .select("id, name, project_id, environment")
    .eq("is_self_monitoring", true);

  const ids = (selfDeployments ?? []).map((d) => d.id);

  const { data: reports } = ids.length
    ? await supabase
        .from("app_issues")
        .select(REPORT_SELECT)
        .in("deployment_id", ids)
        .order("last_seen_at", { ascending: false })
        .limit(500)
    : { data: [] };

  const { data: fixPrompt } = await supabase.rpc(
    "effective_system_issue_fix_prompt",
  );

  return (
    <div className="flex w-full flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">System issues</h1>
        <p className="text-sm text-muted-foreground">
          Errors Build Mill itself has reported. Mark them fixed as you fix
          them; copy one straight into an LLM when you want it fixed now.
        </p>
      </div>
      <SystemIssuesConsole
        initialReports={(reports ?? []) as unknown as ReportRow[]}
        deploymentsConfigured={ids.length > 0}
        fixPrompt={typeof fixPrompt === "string" ? fixPrompt : ""}
      />
    </div>
  );
}
