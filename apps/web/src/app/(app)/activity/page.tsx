import { redirect } from "next/navigation";
import { Bot, User } from "lucide-react";
import { createClient } from "@/lib/supabase/server";
import { resolveActiveOrg } from "@/lib/active-org";
import { fetchActorNames } from "@/lib/approvals";
import {
  readGlobalProjectIds,
  resolveGlobalSelection,
} from "@/lib/global-project-selection";
import { GlobalProjectFilter } from "@/components/global-project-filter";
import { PageHeader } from "@/components/page-header";
import { ActivityFeed, type ActivityRow } from "./activity-feed";

/** us-5.34: the org-wide activity feed — who did what across the whole
 * factory, success at a glance, failure with the story. Read model only:
 * the activity_feed view unions the event sources that already exist. */
export default async function ActivityPage() {
  const supabase = await createClient();

  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  // US-9.11: scope to the active org (US-9.7) — no cross-org leakage.
  const { orgId } = await resolveActiveOrg(supabase, user.id);
  if (!orgId) redirect("/login");

  const { data: projectRows } = await supabase
    .from("projects")
    .select("id, name")
    .eq("org_id", orgId);
  const projects = projectRows ?? [];
  const storedProjectIds = await readGlobalProjectIds();
  const selectedIds = resolveGlobalSelection(projects, storedProjectIds);

  const { data: rows } = await supabase
    .from("activity_feed")
    .select(
      "id, project_id, project_name, kind, action, object_type, object_id, object_label, actor_type, actor_id, actor_name, outcome, detail, created_at"
    )
    .eq("org_id", orgId)
    .order("created_at", { ascending: false })
    .limit(400);

  // US-9.11 presence-lite: principals holding an active claim/lease right now,
  // derived from unexpired leases (US-3.2) — not a separate heartbeat system.
  const { data: leases } = await supabase
    .from("runs")
    .select("id, kind, project_id, workers(name, type)")
    .eq("org_id", orgId)
    .eq("status", "running")
    .gt("claim_expires_at", new Date().toISOString())
    .not("worker_id", "is", null);

  const working = ((leases ?? []) as unknown as Array<{
    kind: string;
    project_id: string | null;
    workers: { name: string | null; type: "autonomous" | "human" } | null;
  }>)
    .filter((l) => !l.project_id || selectedIds.has(l.project_id))
    .map((l) => ({
      name: l.workers?.name ?? "worker",
      isAgent: l.workers?.type === "autonomous",
      kind: l.kind,
    }));

  const feedRows: ActivityRow[] = (rows ?? [])
    .filter((r) => !r.project_id || selectedIds.has(r.project_id))
    .map((r) => ({
    id: r.id ?? "",
    projectId: r.project_id,
    project: r.project_name ?? "",
    kind: r.kind ?? "",
    action: r.action ?? "",
    objectType: r.object_type ?? "",
    objectId: r.object_id,
    objectLabel: r.object_label ?? "",
    actorType: r.actor_type ?? "system",
    actorId: r.actor_id,
    actorName: r.actor_name ?? "",
    outcome: r.outcome ?? "success",
    detail: (r.detail ?? {}) as Record<string, unknown>,
    createdAt: r.created_at ?? "",
  }));

  // Users are stored as ids; workers carry their names in the row.
  const actorNames = await fetchActorNames(
    supabase,
    feedRows
      .filter((r) => r.actorType === "user")
      .map((r) => r.actorId)
  );

  return (
    <div className="flex w-full flex-col gap-6">
      <PageHeader
        title="Activity"
        description="Who did what across the factory — one line per successful step, the full story for failures."
        filter={
          projects.length > 0 && (
            <GlobalProjectFilter
              projects={projects}
              initialSelected={[...selectedIds]}
            />
          )
        }
      />
      {working.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 rounded-md border bg-muted/30 px-3 py-2">
          <span className="text-xs font-medium text-muted-foreground">Working now</span>
          {working.map((w, i) => (
            <span
              key={i}
              className="inline-flex items-center gap-1.5 rounded-full border bg-background px-2.5 py-0.5 text-xs font-medium"
            >
              <span className="relative flex size-1.5">
                <span className="absolute inline-flex size-full animate-ping rounded-full bg-emerald-400 opacity-75" />
                <span className="relative inline-flex size-1.5 rounded-full bg-emerald-500" />
              </span>
              {w.isAgent ? <Bot className="size-3.5" /> : <User className="size-3.5" />}
              {w.name}
              <span className="text-muted-foreground">· {w.kind}</span>
            </span>
          ))}
        </div>
      )}
      <ActivityFeed rows={feedRows} actorNames={actorNames} orgId={orgId} />
    </div>
  );
}
