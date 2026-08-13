"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import Link from "next/link";
import { FileText, History } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { GUIDELINE_CATALOG } from "@/lib/project-guidelines-catalog";
import { EmptyState } from "@/components/empty-state";
import { Button } from "@/components/ui/button";
import { AddGuidelineSection } from "./add-guideline-section";
import { RefreshGuidelinesDialog } from "./refresh-guidelines-dialog";
import { DownloadGuidelinesButton } from "./download-guidelines-button";
import {
  EnvironmentCard,
  type ProjectEnvironment,
} from "./environment-card";
import { SaveInstructionsButton } from "./save-instructions-button";
import { MarkReadyControl } from "./mark-ready-control";
import {
  GuidelineSectionCard,
  type GuidelineSectionRow,
} from "./guideline-section-card";

const ESSENTIAL_KEYS = new Set(
  GUIDELINE_CATALOG.filter((s) => s.essential).map((s) => s.key)
);

export function GuidelinesTab({
  canRefresh,
  hasRepo,
  orgId,
  projectId,
  projectName,
  sections,
  environment,
  guidelinesReadyAt,
  guidelinesReadyByName,
  instructionsSyncedAt,
  instructionsSyncedSha,
  repoFullName,
  defaultBranch,
}: {
  canRefresh: boolean;
  hasRepo: boolean;
  orgId: string;
  projectId: string;
  projectName: string;
  sections: GuidelineSectionRow[];
  environment: ProjectEnvironment;
  guidelinesReadyAt: string | null;
  guidelinesReadyByName: string | null;
  instructionsSyncedAt: string | null;
  instructionsSyncedSha: string | null;
  repoFullName: string | null;
  defaultBranch: string | null;
}) {
  const router = useRouter();
  const [reordering, setReordering] = useState(false);
  const [draggedId, setDraggedId] = useState<string | null>(null);

  const existingKeys = sections.map((s) => s.section_key);
  const nextSortOrder = sections.length
    ? Math.max(...sections.map((s) => s.sort_order)) + 1
    : 0;

  // US-7.4: "edited since marked ready" — any section touched after the stamp.
  const editedSinceReady =
    !!guidelinesReadyAt &&
    sections.some((s) => s.updated_at && s.updated_at > guidelinesReadyAt);

  async function persistOrder(orderedIds: string[]) {
    setReordering(true);
    const supabase = createClient();
    await Promise.all(
      orderedIds.map((id, index) =>
        supabase.from("project_guidelines").update({ sort_order: index }).eq("id", id)
      )
    );
    setReordering(false);
    router.refresh();
  }

  async function handleMove(id: string, direction: "up" | "down") {
    const idx = sections.findIndex((s) => s.id === id);
    const swapIdx = direction === "up" ? idx - 1 : idx + 1;
    if (idx === -1 || swapIdx < 0 || swapIdx >= sections.length) return;

    const a = sections[idx];
    const b = sections[swapIdx];
    setReordering(true);
    const supabase = createClient();
    await Promise.all([
      supabase
        .from("project_guidelines")
        .update({ sort_order: b.sort_order })
        .eq("id", a.id),
      supabase
        .from("project_guidelines")
        .update({ sort_order: a.sort_order })
        .eq("id", b.id),
    ]);
    setReordering(false);
    router.refresh();
  }

  function handleDelete(id: string) {
    // router.refresh() (triggered by the card itself) re-fetches; this
    // just avoids a stale flash of the deleted card before that resolves.
    void id;
  }

  // The dragged id is carried via dataTransfer (read synchronously on drop)
  // rather than through the draggedId *state* — state updates from
  // dragstart aren't guaranteed to have flushed by the time drop fires,
  // so a state-only read here can silently no-op. draggedId state is kept
  // only for the drag-in-progress visual (opacity on the source card).
  function handleDragStart(id: string) {
    return (e: React.DragEvent) => {
      setDraggedId(id);
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", id);
    };
  }

  function handleDragEnd() {
    setDraggedId(null);
  }

  function handleDragOver(e: React.DragEvent) {
    e.preventDefault();
  }

  function handleDrop(targetId: string) {
    return (e: React.DragEvent) => {
      e.preventDefault();
      const sourceId = e.dataTransfer.getData("text/plain") || draggedId;
      setDraggedId(null);
      if (!sourceId || sourceId === targetId) return;

      const ids = sections.map((s) => s.id);
      const fromIdx = ids.indexOf(sourceId);
      const toIdx = ids.indexOf(targetId);
      if (fromIdx === -1 || toIdx === -1) return;

      const reordered = [...ids];
      reordered.splice(fromIdx, 1);
      reordered.splice(toIdx, 0, sourceId);
      persistOrder(reordered);
    };
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <SaveInstructionsButton projectId={projectId} />
            <MarkReadyControl
              projectId={projectId}
              prefix="guidelines"
              readyAt={guidelinesReadyAt}
              readyByName={guidelinesReadyByName}
              editedSince={editedSinceReady}
            />
          </div>
          {/* US-22.7: the repo is written automatically before the next
              agent starts, so "edited since ready" now means "not yet
              pushed" — honest and short-lived. Saying what the repo
              actually holds is what makes that readable. */}
          <p className="text-xs text-muted-foreground">
            {instructionsSyncedAt ? (
              <>
                Repo instructions written{" "}
                {new Date(instructionsSyncedAt).toLocaleDateString(undefined, {
                  month: "short",
                  day: "numeric",
                  hour: "numeric",
                  minute: "2-digit",
                })}
                {instructionsSyncedSha && repoFullName ? (
                  <>
                    {" · "}
                    <a
                      className="underline underline-offset-2 hover:text-foreground"
                      href={`https://github.com/${repoFullName}/commit/${instructionsSyncedSha}`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {instructionsSyncedSha.slice(0, 7)}
                    </a>
                  </>
                ) : null}
                {editedSinceReady
                  ? " · edited since — the next dispatch will push it"
                  : null}
              </>
            ) : (
              <>
                Not yet written to{" "}
                {repoFullName ? (
                  <code>
                    {repoFullName}@{defaultBranch || "main"}
                  </code>
                ) : (
                  "a repository"
                )}
                . The next dispatch will push it.
              </>
            )}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            render={
              <Link href={`/projects/${projectId}/audit?surface=guidelines`} />
            }
            title="Who changed these guidelines, and what they said before"
          >
            <History className="size-4" />
            History
          </Button>
          {canRefresh ? (
            <RefreshGuidelinesDialog projectId={projectId} hasRepo={hasRepo} />
          ) : null}
          <DownloadGuidelinesButton projectId={projectId} projectName={projectName} />
          <AddGuidelineSection
            orgId={orgId}
            projectId={projectId}
            existingKeys={existingKeys}
            nextSortOrder={nextSortOrder}
          />
        </div>
      </div>

      {/* us-5.23: the declared environment, beside the run-commands
          section it complements — what workers get told, structured. */}
      <EnvironmentCard projectId={projectId} environment={environment} />

      {!sections.length ? (
        <EmptyState
          icon={FileText}
          title="No guidelines yet"
          description="Add a section from the catalog so every run gets this project's context without you re-explaining it."
        />
      ) : (
        <div className="grid gap-3">
          {sections.map((s, i) => (
            <div
              key={s.id}
              onDragOver={handleDragOver}
              onDrop={handleDrop(s.id)}
            >
              <GuidelineSectionCard
                section={s}
                orgId={orgId}
                essential={ESSENTIAL_KEYS.has(
                  s.section_key as (typeof GUIDELINE_CATALOG)[number]["key"]
                )}
                isFirst={i === 0 || reordering}
                isLast={i === sections.length - 1 || reordering}
                isDragging={draggedId === s.id}
                onMove={handleMove}
                onDelete={handleDelete}
                onDragHandleStart={handleDragStart(s.id)}
                onDragHandleEnd={handleDragEnd}
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
