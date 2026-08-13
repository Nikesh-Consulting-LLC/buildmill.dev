// US-16.6: what the Reports hub reads. One row per distinct problem — the
// occurrence count is the number of times it happened, not the number of rows.

export const REPORT_SELECT =
  "id, org_id, project_id, deployment_id, source, fingerprint, occurrence_count," +
  " first_seen_at, last_seen_at, title, message, stack_trace, context," +
  " reporter_name, reporter_email, status, promoted_issue_id, triaged_at, created_at";

export type ReportRow = {
  id: string;
  org_id: string;
  project_id: string;
  deployment_id: string;
  source: "automated" | "user_report" | string;
  fingerprint: string | null;
  occurrence_count: number;
  first_seen_at: string;
  last_seen_at: string;
  title: string;
  message: string | null;
  stack_trace: string | null;
  context: Record<string, unknown> | null;
  reporter_name: string | null;
  reporter_email: string | null;
  status: "new" | "triaged" | "promoted" | "ignored" | "fixed" | string;
  promoted_issue_id: string | null;
  triaged_at: string | null;
  created_at: string;
};

export type ReportDeployment = {
  id: string;
  name: string;
  project_id: string;
  environment: string | null;
};

/** Open means "still needs a decision" — the hub's default view. */
export const OPEN_STATUSES = ["new", "triaged"];
