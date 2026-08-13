"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Hammer, Loader2, NotebookPen } from "lucide-react";
import { apiCall, ApiError } from "@/lib/api";
import { createClient } from "@/lib/supabase/client";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { toastError, toastSuccess } from "@/components/ui/toast";
import { DispatchPreviewDialog } from "@/components/instruction-preview";

/** US-49.7: dispatch every story from the list they are in.
 *
 * The batch itself is `dispatch_feature_batch` behind `/batch-dispatch`, which
 * the cockpit rail has offered since us-20.6. This is the same action on the
 * tab that holds the stories — a manager reading 39 rows should not have to go
 * back up to the rail to act on them.
 *
 * Two buttons issuing one POST is what us-12.1 removed, and it is only
 * defensible here because they share everything that could drift: the same
 * `feature_dispatch_phase` RPC decides the phase, the same dialog confirms it,
 * the same endpoint runs it. */
type PhaseInfo = {
  phase: string;
  reason: string;
  children: number;
  buildable: number;
  same_stage: boolean;
  build_mode: string;
};

export function BulkDispatchButton({
  featureId,
  orgId,
  projectId,
}: {
  featureId: string;
  orgId: string;
  projectId: string;
}) {
  const router = useRouter();
  const [info, setInfo] = useState<PhaseInfo | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [draw, setDraw] = useState(false);
  /** US-49.7: one instruction the manager edits once for the whole batch,
   * opened with the project's own default for the phase. Without it the
   * batch dialog was the one dispatch in the app that showed a count where
   * every other shows what the agent will read. */
  const [common, setCommon] = useState("");
  const [prefilled, setPrefilled] = useState(false);
  /** How many stories the common text would actually reach: the ones that
   * carry nothing of their own. A story with its own instructions keeps
   * them — the manager's words for THAT story beat a default typed for the
   * batch, the same rule the seeder has always followed. */
  const [blanks, setBlanks] = useState<string[]>([]);
  const [childCount, setChildCount] = useState(0);

  const load = useCallback(async () => {
    const supabase = createClient();
    const { data } = await supabase.rpc("feature_dispatch_phase", {
      p_feature: featureId,
    });
    setInfo((data as unknown as PhaseInfo) ?? null);
  }, [featureId]);

  useEffect(() => {
    void load();
  }, [load]);

  /** The common instruction, and who it would reach. Read when the dialog
   * opens rather than on mount — nothing here is worth a query on a page the
   * manager is only reading. */
  const loadCommon = useCallback(
    async (kind: "plan" | "code") => {
      const supabase = createClient();
      const { data: children } = await supabase
        .from("issues")
        .select("id, instruction_set")
        .eq("parent_id", featureId)
        .is("abandoned_at", null);
      const rows = children ?? [];
      setChildCount(rows.length);
      setBlanks(
        rows
          .filter((c) => !(c.instruction_set ?? "").trim())
          .map((c) => c.id as string)
      );

      if (common.trim()) return;
      const { data: row } = await supabase
        .from("worker_instructions")
        .select("content")
        .eq("project_id", projectId)
        .eq("run_kind", kind)
        .maybeSingle();
      let text = (row?.content ?? "").trim();
      if (!text) {
        const { data } = await supabase.rpc("default_worker_instruction", {
          p_kind: kind,
        });
        text = ((data as string | null) ?? "").trim();
      }
      if (text) {
        setCommon(`## Expectations — ${kind} run\n\n${text}`);
        setPrefilled(true);
      }
    },
    [featureId, projectId, common]
  );

  // Absent, not disabled: "these are at different stages" is not an error the
  // manager caused by clicking, and a greyed button invites the click anyway.
  if (!info || !info.same_stage) return null;
  if (info.phase !== "plan" && info.phase !== "code") return null;

  const planning = info.phase === "plan";
  const count = planning ? info.children : info.buildable;
  if (count < 1) return null;

  // In feature/epic mode the FEATURE owns the build, so a code batch is one
  // run carrying every story — and one instruction set, the feature's own.
  const featureOwnsBuild = ["feature", "epic"].includes(info.build_mode);
  const oneRun = !planning && featureOwnsBuild;

  /** Write the common text onto the stories that carry nothing of their own,
   * before anything is queued. The seeder skips an item that already has
   * instructions, so each of those runs then reads exactly what was on
   * screen — the same reason the single-item dialog can edit and dispatch
   * without new plumbing (us-49.1). */
  async function applyCommon() {
    const text = common.trim();
    if (!text || blanks.length === 0) return;
    const supabase = createClient();
    const { error } = await supabase
      .from("issues")
      .update({ instruction_set: text })
      .in("id", blanks);
    if (error) throw new Error(error.message);
    await supabase.from("issue_events").insert(
      blanks.map((id) => ({
        org_id: orgId,
        issue_id: id,
        type: "instructions-updated",
        payload: { length: text.length, at: "batch-dispatch" },
      }))
    );
  }

  async function go() {
    setBusy(true);
    try {
      if (!draw && !oneRun) await applyCommon();
      if (draw) {
        const r = (await apiCall(
          `/api/v1/issues/${featureId}/wireframes/batch-dispatch`,
          { method: "POST" }
        )) as { dispatched_count: number; skipped_count: number };
        toastSuccess(
          r.dispatched_count === 0
            ? `Nothing to draw — all ${r.skipped_count} stories are already drawn or in flight`
            : `${r.dispatched_count} queued for drawing` +
                (r.skipped_count ? ` · ${r.skipped_count} skipped` : "")
        );
      } else {
        await apiCall(`/api/v1/issues/${featureId}/batch-dispatch`, {
          method: "POST",
        });
        toastSuccess(
          planning ? `Planning ${count} stories` : `Building ${count} stories`,
          "Queued — they start as agents pick them up."
        );
      }
      setOpen(false);
      setDraw(false);
      router.refresh();
      void load();
    } catch (e) {
      toastError(
        "Couldn't dispatch the batch",
        e instanceof ApiError ? String(e.message) : (e as Error).message
      );
    } finally {
      setBusy(false);
    }
  }

  const noun = count === 1 ? "story" : "stories";

  return (
    <>
      <Button
        variant="outline"
        size="sm"
        onClick={() => {
          setOpen(true);
          // Opening from here never reaches the Dialog's onOpenChange.
          void loadCommon(planning ? "plan" : "code");
        }}
        title={info.reason}
      >
        {busy ? (
          <Loader2 className="size-4 animate-spin" />
        ) : planning ? (
          <NotebookPen className="size-4" />
        ) : (
          <Hammer className="size-4" />
        )}
        {planning ? `Plan all ${count}` : `Build all ${count}`}
      </Button>

      <DispatchPreviewDialog
        issueId={featureId}
        orgId={orgId}
        kind={planning ? "plan" : "code"}
        open={open}
        onOpenChange={(o) => {
          setOpen(o);
          if (o) void loadCommon(planning ? "plan" : "code");
          if (!o) setDraw(false);
        }}
        onConfirm={go}
        busy={busy}
        title={planning ? `Plan ${count} ${noun}` : `Build ${count} ${noun}`}
        summary={
          draw
            ? `${count} ${noun} — one drawing run each. Stories already drawn are skipped.`
            : oneRun
              ? `${count} ${noun} as one run — one branch, one PR, one review covering all of them. They share the feature's instruction set:`
              : planning
                ? `${count} ${noun}, each with its own run and its own instruction set — open a story to see or change its own.`
                : `${count} ${noun}, one run each, drained one at a time.`
        }
        // Only a code batch the FEATURE owns has a single set to show; every
        // other shape is one run per story, and gets the common instruction
        // below instead of fifteen scrolling documents.
        showPreview={oneRun}
        body={
          <div className="flex flex-col gap-2">
            <div className="flex items-baseline justify-between gap-3">
              <span className="text-sm font-medium">
                Instructions for all {count} {noun}
              </span>
              <span className="text-xs text-muted-foreground">
                {prefilled
                  ? `the project's ${planning ? "plan" : "code"} default — edit it for this batch`
                  : "edited for this batch"}
              </span>
            </div>
            <Textarea
              rows={12}
              className="max-h-[40vh] font-mono text-xs"
              value={common}
              onChange={(e) => {
                setCommon(e.target.value);
                setPrefilled(false);
              }}
              placeholder="What every agent in this batch is expected to do."
            />
            <p className="text-xs text-muted-foreground">
              {blanks.length === childCount
                ? `Saved onto all ${blanks.length} ${blanks.length === 1 ? "story" : "stories"} before they queue.`
                : `Saved onto the ${blanks.length} ${
                    blanks.length === 1 ? "story" : "stories"
                  } that carry nothing of their own — the other ${
                    childCount - blanks.length
                  } keep theirs untouched.`}
            </p>
          </div>
        }
        wireframe={
          planning
            ? {
                checked: draw,
                onChange: setDraw,
                note: `Each of the ${count} ${noun} gets its own metered drawing run. Plan them once the drawings land.`,
              }
            : undefined
        }
        confirmLabel={planning ? `Plan ${count}` : `Build ${count}`}
      />
    </>
  );
}
