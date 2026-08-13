import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { createClient } from "@/lib/supabase/server";
import { Button } from "@/components/ui/button";
import { GUIDELINE_CATALOG } from "@/lib/project-guidelines-catalog";
import { RefreshReview, type ProposedSection } from "./refresh-review";

// Catalog order is the order the assembled document reads in. The bundle is
// sorted by it — NOT by severity — because the question being answered here is
// "do these guidelines describe my project", and that is read top to bottom.
const CATALOG_ORDER = new Map(
  GUIDELINE_CATALOG.map((s, i) => [s.key as string, i])
);

export default async function GuidelinesRefreshReviewPage({
  params,
}: {
  params: Promise<{ id: string; refreshId: string }>;
}) {
  const { id, refreshId } = await params;
  const supabase = await createClient();

  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const { data: refresh } = await supabase
    .from("guideline_refreshes")
    .select(
      "id, project_id, status, summary, scope, focus, created_at, workers(name), projects!inner(id, name)"
    )
    .eq("id", refreshId)
    .eq("project_id", id)
    .maybeSingle();
  if (!refresh) notFound();

  const { data: rows } = await supabase
    .from("guideline_recommendations")
    .select(
      "id, section_key, section_title, section_id, severity, proposed_text, rationale, status, decision_note, project_guidelines(content)"
    )
    .eq("refresh_id", refreshId)
    .order("created_at", { ascending: true });

  const sections: ProposedSection[] = (rows ?? [])
    .map((r) => {
      const stored = r.project_guidelines as unknown as
        | { content: string }
        | { content: string }[]
        | null;
      return {
        id: r.id as string,
        sectionKey: (r.section_key as string) ?? "",
        title: r.section_title as string,
        severity: r.severity as string,
        rationale: r.rationale as string,
        proposedText: r.proposed_text as string,
        // A proposal against a section that does not exist yet is an
        // addition, not a diff against nothing — the client renders it as one.
        isNew: r.section_id === null,
        currentText:
          (Array.isArray(stored) ? stored[0]?.content : stored?.content) ?? "",
        status: r.status as string,
        decisionNote: (r.decision_note as string) ?? "",
      };
    })
    .sort(
      (a, b) =>
        (CATALOG_ORDER.get(a.sectionKey) ?? 99) -
        (CATALOG_ORDER.get(b.sectionKey) ?? 99)
    );

  const project = refresh.projects as unknown as { id: string; name: string };
  const worker = refresh.workers as unknown as { name: string } | null;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            render={<Link href={`/projects/${id}?tab=guidelines`} />}
          >
            <ArrowLeft className="size-4" />
            {project?.name ?? "Project"}
          </Button>
        </div>

      </div>

      <RefreshReview
        status={refresh.status as string}
        summary={(refresh.summary as string) ?? ""}
        scope={(refresh.scope as string) ?? "all"}
        focus={(refresh.focus as string) ?? ""}
        workerName={worker?.name ?? "an agent"}
        sections={sections}
      />
    </div>
  );
}
