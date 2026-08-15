"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "@/lib/router-with-progress";
import { History, Loader2, RotateCcw } from "lucide-react";
import { cn } from "@/lib/utils";
import { createClient } from "@/lib/supabase/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { MarkdownEditor } from "@/components/markdown-editor";
import { MarkReadyControl } from "./mark-ready-control";
import { TaskProcessingCard } from "./task-processing-card";

export type WorkerInstructionRow = {
  id: string;
  run_kind: string;
  content: string;
  updated_by: string | null;
  updated_at: string;
};

const KIND_META: Record<string, { title: string; description: string }> = {
  prd: {
    title: "PRD runs",
    description: "How a worker should draft a feature's PRD.",
  },
  breakdown: {
    title: "Story breakdown runs",
    description:
      "How a worker should split an approved PRD into engineering stories.",
  },
  plan: {
    title: "Stories in a feature — plan",
    description:
      "How a worker should write implementation and test plans for a story born from a PRD breakdown.",
  },
  code: {
    title: "Stories in a feature — build",
    description:
      "How a worker should implement a feature-child story's approved plan.",
  },
  standalone_plan: {
    title: "Standalone stories — plan",
    description:
      "Planning a story with no PRD and no parent feature — the story and its acceptance criteria are the whole contract.",
  },
  standalone_code: {
    title: "Standalone stories — build",
    description:
      "Implementing a standalone story's approved plan, inside this story's slice only.",
  },
  bug_rca: {
    title: "Bugs — root cause analysis",
    description:
      "How a worker should diagnose a bug: what broke, why, and the proposed fix — in plain language, no code.",
  },
  bug_fix: {
    title: "Bugs — the fix",
    description:
      "How a worker should implement an approved RCA's proposed fix, with the reproduction as the regression case.",
  },
  chore: {
    title: "Chores — single-shot build",
    description:
      "How a worker should build a chore directly — no plan phase precedes it, and the hand-back notes carry the verification story.",
  },
  test: {
    title: "Test runs",
    description:
      "How a worker should execute a work item's test cases and report results.",
  },
  release: {
    title: "Release runs",
    description:
      "How a worker should assemble a release: read the change range, write the notes, deploy to UAT and verify it.",
  },
  deploy: {
    title: "Deployment runs",
    description:
      "How a worker should trigger, observe and verify one deployment — never claiming an outcome it did not see.",
  },
  test_case_elaborate: {
    title: "Test-case elaboration",
    description:
      "Extra guidance appended when a rough test description is expanded into a full manual test case.",
  },
  deploy_script_generate: {
    title: "Deploy-script generation",
    description:
      "Extra guidance appended when a deployment script is drafted for this project.",
  },
};

/** US-20.1: six sections, Task processing first. `kinds: null` marks the
 * section that renders settings rather than instruction rows. Any run kind
 * absent from every section falls into "Other" (below) — three kinds were
 * added by migrations 085/112/114 and rendered here as raw slugs for
 * months because the tab was written when there were only four. */
const SECTIONS: {
  key: string;
  label: string;
  blurb: string;
  kinds: string[] | null;
}[] = [
  {
    key: "task-processing",
    label: "Task processing",
    blurb:
      "How this project's work flows through the factory before any of the instructions below apply.",
    kinds: null,
  },
  {
    key: "requirements",
    label: "Requirements",
    blurb:
      "Turning an idea into a specification, and a specification into stories.",
    kinds: ["prd", "breakdown"],
  },
  {
    key: "planning",
    label: "Planning",
    blurb:
      "Deciding how work will be built and verified — each work-item type in its own words (us-96.3).",
    kinds: ["plan", "standalone_plan", "bug_rca"],
  },
  {
    key: "coding",
    label: "Coding",
    blurb:
      "Writing the change and handing it back for review — each work-item type in its own words (us-96.3).",
    kinds: ["code", "standalone_code", "bug_fix", "chore"],
  },
  {
    key: "testing",
    label: "Testing",
    blurb: "Executing test cases and reporting what actually happened.",
    kinds: ["test"],
  },
  {
    key: "release",
    label: "Release",
    blurb: "Assembling a release, shipping it, and verifying where it landed.",
    kinds: ["release", "deploy"],
  },
];

const MAPPED_KINDS = new Set(SECTIONS.flatMap((s) => s.kinds ?? []));
const OTHER_KEY = "other";

function formatUpdatedAt(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

/** US-5.14: the project's editable behavioral instructions per run kind.
 * Mechanics (branch, remote, auth, submit tool) stay code-generated on the
 * API side — this text is the guidance layered on top, served live to
 * workers over the REST context endpoint and MCP get_work_context.
 *
 * US-20.1: the draft lives in the parent, not here. Behind a nav, local
 * draft state would be thrown away every time the manager switched
 * sections — a data-loss path the flat page never had. */
function InstructionCard({
  row,
  actorNames,
  orgId,
  draft,
  onDraftChange,
}: {
  row: WorkerInstructionRow;
  actorNames: Record<string, string>;
  orgId: string;
  draft: string | undefined;
  onDraftChange: (next: string | undefined) => void;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState<"save" | "reset" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const content = draft ?? row.content;
  const dirty = content !== row.content;
  const meta = KIND_META[row.run_kind] ?? {
    title: row.run_kind,
    description: "",
  };

  async function save(next: string) {
    setError(null);
    const supabase = createClient();
    const { error: dbError } = await supabase
      .from("worker_instructions")
      .update({ content: next })
      .eq("id", row.id);
    if (dbError) {
      setError(dbError.message);
      return false;
    }
    router.refresh();
    return true;
  }

  async function handleSave() {
    setBusy("save");
    // The row prop refreshes to the saved text, so the draft must clear —
    // otherwise it keeps shadowing it and the card reads dirty forever.
    if (await save(content)) onDraftChange(undefined);
    setBusy(null);
  }

  async function handleReset() {
    setBusy("reset");
    setError(null);
    const supabase = createClient();
    const { data, error: rpcError } = await supabase.rpc(
      "default_worker_instruction",
      { p_kind: row.run_kind }
    );
    if (rpcError || !data) {
      setError(rpcError?.message ?? "Could not load the factory default.");
      setBusy(null);
      return;
    }
    if (await save(data)) onDraftChange(undefined);
    setBusy(null);
  }

  return (
    <Card className="min-w-0">
      <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0">
        <div className="min-w-0 space-y-1.5">
          <CardTitle className="text-base">{meta.title}</CardTitle>
          <CardDescription>{meta.description}</CardDescription>
        </div>
        <Button
          variant="outline"
          size="sm"
          className="shrink-0"
          disabled={busy !== null}
          onClick={handleReset}
        >
          {busy === "reset" ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <RotateCcw className="size-3.5" />
          )}
          Reset to default
        </Button>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <MarkdownEditor
          rows={7}
          value={content}
          onChange={(next) => onDraftChange(next)}
          orgId={orgId}
        />
        <div className="flex items-center justify-between gap-3">
          <p className="text-xs text-muted-foreground">
            {row.updated_by ? (
              <>
                Last edited {formatUpdatedAt(row.updated_at)} by{" "}
                {actorNames[row.updated_by] ?? "a member"}
              </>
            ) : (
              <Badge variant="secondary" className="font-normal">
                factory default
              </Badge>
            )}
          </p>
          {dirty && (
            <Button size="sm" disabled={busy !== null} onClick={handleSave}>
              {busy === "save" && <Loader2 className="size-4 animate-spin" />}
              Save
            </Button>
          )}
        </div>
        {error && (
          <p className="text-sm font-medium text-destructive">{error}</p>
        )}
      </CardContent>
    </Card>
  );
}

export function WorkerInstructionsTab({
  rows,
  actorNames,
  orgId,
  projectId,
  readyAt,
  readyByName,
  initialSection,
  followBuildOrder,
  routeFeatureAsOne,
  autoApprovePrd,
  autoApprovePlan,
  autoApproveCode,
}: {
  rows: WorkerInstructionRow[];
  actorNames: Record<string, string>;
  orgId: string;
  projectId: string;
  readyAt: string | null;
  readyByName: string | null;
  /** US-20.1: `?section=` deep link. Unknown or absent lands on the first. */
  initialSection?: string;
  /** US-86.1: the two routing switches. */
  followBuildOrder: boolean;
  routeFeatureAsOne: boolean;
  autoApprovePrd: boolean;
  autoApprovePlan: boolean;
  autoApproveCode: boolean;
}) {
  const unmapped = rows.filter((r) => !MAPPED_KINDS.has(r.run_kind));
  const sections = unmapped.length
    ? [
        ...SECTIONS,
        {
          key: OTHER_KEY,
          label: "Other",
          blurb:
            "Run kinds the factory serves that this page has no section for yet.",
          kinds: unmapped.map((r) => r.run_kind),
        },
      ]
    : SECTIONS;

  const [active, setActive] = useState(
    sections.some((s) => s.key === initialSection)
      ? (initialSection as string)
      : sections[0].key
  );
  // Drafts by instruction row id, held here so switching sections (which
  // unmounts the pane) never discards what the manager typed.
  const [drafts, setDrafts] = useState<Record<string, string>>({});

  function setDraft(rowId: string, next: string | undefined) {
    setDrafts((prev) => {
      if (next === undefined) {
        if (!(rowId in prev)) return prev;
        const { [rowId]: _drop, ...rest } = prev;
        return rest;
      }
      return { ...prev, [rowId]: next };
    });
  }

  const rowsForKinds = (kinds: string[] | null) =>
    kinds === null
      ? []
      : kinds
          .map((k) => rows.find((r) => r.run_kind === k))
          .filter((r): r is WorkerInstructionRow => !!r);

  // US-7.5: "edited since marked ready" — any block touched after the stamp.
  const editedSinceReady =
    !!readyAt && rows.some((r) => r.updated_at && r.updated_at > readyAt);

  const sectionFlags = (kinds: string[] | null) => {
    const sectionRows = rowsForKinds(kinds);
    return {
      unsaved: sectionRows.some(
        (r) => drafts[r.id] !== undefined && drafts[r.id] !== r.content
      ),
      // The tab-level warning no longer says WHERE, so the nav does.
      editedSince:
        !!readyAt && sectionRows.some((r) => r.updated_at > readyAt),
    };
  };

  const activeSection = sections.find((s) => s.key === active) ?? sections[0];

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <MarkReadyControl
          projectId={projectId}
          prefix="worker_instructions"
          readyAt={readyAt}
          readyByName={readyByName}
          editedSince={editedSinceReady}
        />
        <Button
          variant="ghost"
          size="sm"
          className="shrink-0"
          title="Who changed these instructions, and what they said before"
          render={
            <Link
              href={`/projects/${projectId}/audit?surface=worker_instructions`}
            />
          }
        >
          <History className="size-4" />
          History
        </Button>
      </div>

      {/* US-20.1: nav beside the pane on wide windows, a scrolling row above
          it on narrow ones — the us-19.1 treatment, so a section is never
          clipped out of reach. */}
      <div className="flex min-w-0 flex-col gap-4 md:flex-row md:gap-6">
        <nav
          aria-label="Instruction sections"
          className="-mx-1 flex shrink-0 gap-1 overflow-x-auto px-1 pb-1 md:mx-0 md:w-56 md:flex-col md:overflow-visible md:px-0 md:pb-0"
        >
          {sections.map((s) => {
            const flags = sectionFlags(s.kinds);
            return (
              <button
                key={s.key}
                type="button"
                aria-current={active === s.key ? "page" : undefined}
                onClick={() => setActive(s.key)}
                className={cn(
                  "flex shrink-0 items-center gap-2 whitespace-nowrap rounded-md px-3 py-2 text-left text-sm transition-colors md:w-full",
                  active === s.key
                    ? "bg-secondary font-medium text-secondary-foreground"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground"
                )}
              >
                <span className="min-w-0 flex-1 truncate">{s.label}</span>
                {flags.unsaved && (
                  <span
                    className="size-1.5 shrink-0 rounded-full bg-amber-500"
                    title="Unsaved edits in this section"
                  />
                )}
                {!flags.unsaved && flags.editedSince && (
                  <span
                    className="size-1.5 shrink-0 rounded-full bg-muted-foreground/60"
                    title="Edited since this project was marked ready"
                  />
                )}
              </button>
            );
          })}
        </nav>

        <div className="flex min-w-0 flex-1 flex-col gap-4">
          <p className="text-sm text-muted-foreground">
            {activeSection.blurb}
          </p>
          {activeSection.kinds === null ? (
            <TaskProcessingCard
              projectId={projectId}
              followBuildOrder={followBuildOrder}
              routeFeatureAsOne={routeFeatureAsOne}
              autoApprovePrd={autoApprovePrd}
              autoApprovePlan={autoApprovePlan}
              autoApproveCode={autoApproveCode}
            />
          ) : (
            rowsForKinds(activeSection.kinds).map((row) => (
              <InstructionCard
                key={row.id}
                row={row}
                actorNames={actorNames}
                orgId={orgId}
                draft={drafts[row.id]}
                onDraftChange={(next) => setDraft(row.id, next)}
              />
            ))
          )}
        </div>
      </div>
    </div>
  );
}
