import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { createClient } from "@/lib/supabase/server";
import { Button } from "@/components/ui/button";
import { KIND_FILES } from "@/lib/instruction-files";
import { AGENTS_KEY, templateFiles } from "@/lib/template-files";
import { RefreshReview, type ProposedFile } from "./refresh-review";

// us-100.5: a refresh proposes whole FILES — the Agent Instructions document
// (`agents`) and per-task instruction files (a run kind each). The review
// shows the proposal against what the project holds NOW, in publish order:
// AGENTS.md first, then the .buildmill files in the project's group order.
const FILE_ORDER = new Map(templateFiles().map((f, i) => [f.key, i]));

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
      "id, project_id, status, summary, scope, focus, created_at, workers(name), projects!inner(id, name, agent_instructions)"
    )
    .eq("id", refreshId)
    .eq("project_id", id)
    .maybeSingle();
  if (!refresh) notFound();

  const project = refresh.projects as unknown as {
    id: string;
    name: string;
    agent_instructions: string | null;
  };

  const { data: rows } = await supabase
    .from("guideline_recommendations")
    .select(
      "id, section_key, section_title, section_id, severity, proposed_text, rationale, status, decision_note"
    )
    .eq("refresh_id", refreshId)
    .order("created_at", { ascending: true });

  // The current text of every per-task file the pass touches. Resolved the
  // way an agent reads it: the project's own row, else the factory default.
  const kinds = (rows ?? [])
    .map((r) => r.section_key as string)
    .filter((k) => k !== AGENTS_KEY && k in KIND_FILES);
  const current: Record<string, string> = {};
  if (kinds.length) {
    const { data: own } = await supabase
      .from("worker_instructions")
      .select("run_kind, content")
      .eq("project_id", id)
      .in("run_kind", kinds);
    for (const w of own ?? []) current[w.run_kind] = w.content ?? "";
    for (const k of kinds) {
      if (current[k] !== undefined) continue;
      const { data: def } = await supabase.rpc("default_worker_instruction", {
        p_kind: k,
      });
      current[k] = (def as string | null) ?? "";
    }
  }

  const files: ProposedFile[] = (rows ?? [])
    .map((r) => {
      const key = (r.section_key as string) ?? "";
      const isLegacy = r.section_id !== null || !(key === AGENTS_KEY || key in KIND_FILES);
      return {
        id: r.id as string,
        key,
        path: (r.section_title as string) || (key === AGENTS_KEY ? "AGENTS.md" : key),
        severity: r.severity as string,
        rationale: r.rationale as string,
        proposedText: r.proposed_text as string,
        currentText:
          key === AGENTS_KEY
            ? project.agent_instructions ?? ""
            : current[key] ?? "",
        isLegacy,
        status: r.status as string,
        decisionNote: (r.decision_note as string) ?? "",
      };
    })
    .sort(
      (a, b) => (FILE_ORDER.get(a.key) ?? 99) - (FILE_ORDER.get(b.key) ?? 99)
    );

  const worker = refresh.workers as unknown as { name: string } | null;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            render={<Link href={`/projects/${id}?tab=instructions&file=agents`} />}
          >
            <ArrowLeft className="size-4" />
            {project?.name ?? "Project"}
          </Button>
        </div>
      </div>

      <RefreshReview
        refreshId={refreshId}
        projectId={id}
        status={refresh.status as string}
        summary={(refresh.summary as string) ?? ""}
        scope={(refresh.scope as string) ?? "all"}
        focus={(refresh.focus as string) ?? ""}
        workerName={worker?.name ?? "an agent"}
        files={files}
      />
    </div>
  );
}
