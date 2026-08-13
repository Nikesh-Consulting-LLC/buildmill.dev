"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Layers, Loader2 } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { createClient } from "@/lib/supabase/client";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { BREAKDOWN_MODE_ITEMS } from "./prd-panel";

/** Feature → stories breakdown (us-2.33): dispatches a worker `breakdown`
 * run rather than a synchronous LLM call. A worker claims it over MCP,
 * reads the approved PRD + repo + guidelines + learnings, and submits the
 * split — which the factory auto-creates as draft child stories for the
 * manager to curate. The standing mode + manager instructions (us-2.28)
 * steer the run and persist to the feature. Only rendered for a ready
 * feature with no children; while a run is in flight, `pending` shows the
 * queued state instead of the dispatch control. */
export function BreakdownPanel({
  featureId,
  breakdownMode = "automatic",
  breakdownInstructions = "",
  pending = false,
}: {
  featureId: string;
  orgId?: string;
  breakdownMode?: string;
  breakdownInstructions?: string;
  pending?: boolean;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState(breakdownMode || "automatic");
  const [instructions, setInstructions] = useState(breakdownInstructions);

  /** US-11.3: these props carry what is currently saved on the feature, and
   * they change under a mounted panel — approving the PRD writes the
   * manager's mode + instructions and calls `router.refresh()`. Seeding
   * state only on mount meant the panel kept showing the pre-approval
   * values, so dispatching from here silently sent stale instructions (or
   * none) while the manager believed the ones they had just typed were in
   * effect.
   *
   * Adopt a newly-saved value, but never discard an edit in flight: only
   * overwrite when the local value still matches the previously-saved one.
   * Tracking the last-seen prop in state and comparing during render is
   * React's documented way to adjust state on a prop change — it re-renders
   * before painting, with no effect and no flash of the old value. */
  const savedMode = breakdownMode || "automatic";
  const [lastSavedMode, setLastSavedMode] = useState(savedMode);
  if (savedMode !== lastSavedMode) {
    setLastSavedMode(savedMode);
    if (mode === lastSavedMode) setMode(savedMode);
  }

  const [lastSavedInstructions, setLastSavedInstructions] =
    useState(breakdownInstructions);
  if (breakdownInstructions !== lastSavedInstructions) {
    setLastSavedInstructions(breakdownInstructions);
    if (instructions === lastSavedInstructions) {
      setInstructions(breakdownInstructions);
    }
  }

  /** Standing means editable: changed values persist to the feature so the
   * dispatched run's context reads them from the issue row. */
  async function persistStanding() {
    const dirty =
      mode !== (breakdownMode || "automatic") ||
      instructions.trim() !== (breakdownInstructions ?? "").trim();
    if (!dirty) return;
    const supabase = createClient();
    const { error: dbError } = await supabase
      .from("issues")
      .update({
        breakdown_mode: mode,
        breakdown_instructions: instructions.trim() || null,
      })
      .eq("id", featureId);
    if (dbError) throw new Error(dbError.message);
  }

  async function dispatch() {
    setError(null);
    setBusy(true);
    try {
      await persistStanding();
      await apiFetch(`/api/v1/issues/${featureId}/breakdown/dispatch`, {
        method: "POST",
      });
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (pending) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
        Breakdown queued — a worker will pick it up and propose the story
        split.
      </div>
    );
  }

  return (
    <div className="flex flex-col items-start gap-3">
      <p className="text-sm text-muted-foreground">
        Split the approved PRD into engineering stories — a worker drafts the
        split from the PRD and the codebase, and the factory creates each as a
        draft story for you to curate.
      </p>
      <div className="grid w-full gap-3 sm:max-w-md">
        <div className="grid gap-2">
          <Label htmlFor="breakdown-panel-mode">Breakdown</Label>
          <Select
            items={BREAKDOWN_MODE_ITEMS}
            value={mode}
            onValueChange={(v) => {
              if (typeof v === "string") setMode(v);
            }}
          >
            <SelectTrigger id="breakdown-panel-mode" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {BREAKDOWN_MODE_ITEMS.map((item) => (
                <SelectItem key={item.value} value={item.value}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="grid gap-2">
          <Label htmlFor="breakdown-panel-instructions">
            Instructions for the breakdown agent
            <span className="ml-1 font-normal text-muted-foreground">
              (optional)
            </span>
          </Label>
          <Textarea
            id="breakdown-panel-instructions"
            rows={2}
            placeholder="e.g. Keep the API and UI in one story."
            value={instructions}
            onChange={(e) => setInstructions(e.target.value)}
          />
        </div>
      </div>
      <Button onClick={dispatch} disabled={busy}>
        {busy ? (
          <Loader2 className="size-4 animate-spin" />
        ) : (
          <Layers className="size-4" />
        )}
        Dispatch breakdown
      </Button>
      {error && (
        <p className="text-sm font-medium text-destructive">{error}</p>
      )}
    </div>
  );
}
