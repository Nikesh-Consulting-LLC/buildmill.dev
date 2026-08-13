import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { createClient } from "@/lib/supabase/server";
import { fetchActorNames } from "@/lib/approvals";
import { AuditTab, type AuditRow } from "../audit-tab";
import {
  ContentAuditSection,
  type ContentAuditRow,
} from "../content-audit-section";

export default async function ProjectAuditPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ surface?: string }>;
}) {
  const { id } = await params;
  const { surface } = await searchParams;
  const supabase = await createClient();

  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const { data: project } = await supabase
    .from("projects")
    .select("id, name")
    .eq("id", id)
    .maybeSingle();
  if (!project) notFound();

  // approvals is org-scoped with its own RLS on org_id; the inner join
  // narrows it to this project.
  const { data: approvals } = await supabase
    .from("approvals")
    .select(
      "id, gate, decision, subject_type, subject_id, comment, actor, created_at, issues!inner(id, title, project_id)"
    )
    .eq("issues.project_id", project.id)
    .order("created_at", { ascending: false })
    .limit(500);

  const auditRows: AuditRow[] = (approvals ?? []).map((a) => {
    const issue = a.issues as unknown as { id: string; title: string };
    return {
      id: a.id,
      gate: a.gate,
      decision: a.decision,
      subject_type: a.subject_type,
      subject_id: a.subject_id,
      comment: a.comment,
      actor: a.actor,
      created_at: a.created_at,
      issue_id: issue.id,
      issue_title: issue.title,
    };
  });

  const actorNames = await fetchActorNames(
    supabase,
    auditRows.map((r) => r.actor)
  );

  // us-5.33: the content trail — trigger-written, append-only, org RLS.
  const { data: contentAudit } = await supabase
    .from("content_audit")
    .select(
      "id, surface, item_key, action, actor_type, actor_name, before_text, after_text, created_at"
    )
    .eq("project_id", project.id)
    .order("created_at", { ascending: false })
    .limit(500);

  const contentRows: ContentAuditRow[] = contentAudit ?? [];

  return (
    <div className="flex w-full flex-col gap-6">
      <div>
        <Link
          href={`/projects/${project.id}`}
          className="mb-1 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="size-3" />
          {project.name}
        </Link>
        <h1 className="text-2xl font-semibold tracking-tight">Audit</h1>
      </div>
      <AuditTab
        rows={auditRows}
        actorNames={actorNames}
        backToProject={`from=${encodeURIComponent(`/projects/${project.id}/audit`)}&fromLabel=${encodeURIComponent("Audit")}`}
      />
      <ContentAuditSection rows={contentRows} initialSurface={surface} />
    </div>
  );
}
