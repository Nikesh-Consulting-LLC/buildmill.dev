"use client";

// us-100.1 / us-100.3 / us-99.4: the Agent Instructions tab — the document,
// and everything a manager does with it: mark it ready, see whether the
// repository has it, publish it, read its history, or put an agent on
// proposing a better one.
//
// The us-100.1 swap replaced the section editor with the document editor and
// dropped the controls that lived on the old tab's header (Save Instructions,
// Mark ready, History) — leaving no way to publish from the UI. This puts
// them back around the new editor, with us-99.4's publish state where the
// story asked for it: beside the words.

import { useState } from "react";
import Link from "next/link";
import { History } from "lucide-react";

import { Button } from "@/components/ui/button";
import { AgentInstructionsEditor } from "./agent-instructions-editor";
import { MarkReadyControl } from "./mark-ready-control";
import { PublishInstructionsBar } from "./publish-instructions-bar";
import { RefreshGuidelinesDialog } from "./refresh-guidelines-dialog";

export function AgentInstructionsTab({
  projectId,
  initial,
  canEdit,
  repoFullName,
  readyAt,
  readyByName,
  editedSince,
}: {
  projectId: string;
  initial: string;
  canEdit: boolean;
  repoFullName: string | null;
  readyAt: string | null;
  readyByName: string | null;
  editedSince: boolean;
}) {
  const [refreshKey, setRefreshKey] = useState(0);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-2">
          <MarkReadyControl
            projectId={projectId}
            prefix="guidelines"
            readyAt={readyAt}
            readyByName={readyByName}
            editedSince={editedSince}
          />
          <PublishInstructionsBar
            projectId={projectId}
            repoFullName={repoFullName}
            canPublish={canEdit}
            refreshKey={refreshKey}
          />
        </div>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            render={<Link href={`/projects/${projectId}/audit?surface=guidelines`} />}
            title="Who changed the Agent Instructions, and what they said before"
          >
            <History className="size-4" />
            History
          </Button>
          {canEdit ? (
            <RefreshGuidelinesDialog projectId={projectId} hasRepo={!!repoFullName} />
          ) : null}
        </div>
      </div>
      <AgentInstructionsEditor
        projectId={projectId}
        initial={initial}
        canEdit={canEdit}
        onSaved={() => setRefreshKey((k) => k + 1)}
      />
    </div>
  );
}
