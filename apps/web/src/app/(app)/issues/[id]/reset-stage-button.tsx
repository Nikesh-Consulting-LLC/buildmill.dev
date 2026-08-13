"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Eraser, Loader2 } from "lucide-react";
import { apiCall, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

type Stage = "prd" | "elaboration" | "planning" | "coding";

/** US-68.1: what each stage discards and where it lands — stated up front so
 * the manager knows what they're about to lose before confirming. Every
 * stage shares one guarantee, stated separately below: nothing here ever
 * touches GitHub. */
const STAGE_COPY: Record<Stage, { label: string; discards: string }> = {
  prd: {
    label: "PRD",
    discards:
      "deletes the current PRD and abandons its stories — a new PRD means a new breakdown",
  },
  elaboration: {
    label: "Elaboration",
    discards: "discards the elaboration proposal, plan, and test plan",
  },
  planning: {
    label: "Planning",
    discards:
      "discards the plan and test plan, keeping any elaboration proposal",
  },
  coding: {
    label: "Dispatch for Coding",
    discards:
      "keeps the approved plan and re-opens it for a fresh code attempt",
  },
};

/** US-68.1: send a Story/Chore/Feature back to a chosen stage instead of
 * always wiping it to Triage the way "Reset all" (US-15.17) did. Replaces
 * that button outright. Never touches GitHub — a pushed branch or open PR
 * is left exactly as it is, for every stage. */
export function ResetStageButton({
  issueId,
  issueType,
  childCount,
}: {
  issueId: string;
  issueType: string;
  childCount: number;
}) {
  const router = useRouter();
  const isFeature = issueType === "feature";
  const stages: Stage[] = isFeature
    ? ["prd", "elaboration", "planning", "coding"]
    : ["elaboration", "planning", "coding"];

  const [open, setOpen] = useState(false);
  const [stage, setStage] = useState<Stage>(stages[0]);
  const [destination, setDestination] = useState<"draft" | "ready">("draft");
  const [note, setNote] = useState("");
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const blocked = typed !== "reset";

  const cascade =
    isFeature && stage !== "prd" && childCount > 0
      ? ` This cascades to all ${childCount} ${
          childCount === 1 ? "story" : "stories"
        }; the feature's own PRD and status are untouched.`
      : isFeature && stage === "prd" && childCount > 0
        ? ` Its ${childCount} ${
            childCount === 1 ? "story is" : "stories are"
          } abandoned along with it.`
        : "";

  function reset(o: boolean) {
    setOpen(o);
    if (!o) {
      setTyped("");
      setError(null);
      setNote("");
      setStage(stages[0]);
      setDestination("draft");
    }
  }

  async function confirm() {
    setError(null);
    setBusy(true);
    try {
      await apiCall(`/api/v1/issues/${issueId}/reset`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          stage,
          destination_status: stage === "elaboration" ? destination : undefined,
          note: note.trim() || undefined,
        }),
      });
      reset(false);
      router.refresh();
    } catch (e) {
      setError(
        e instanceof ApiError ? String(e.message) : (e as Error).message
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={reset}>
      <DialogTrigger render={<Button variant="outline" size="sm" />}>
        <Eraser className="size-4" />
        Reset
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Send this back to a stage</DialogTitle>
          <DialogDescription>
            This never touches GitHub — any branch or PR already pushed is
            left exactly as it is. If anything in scope has already merged or
            shipped, the reset is blocked; that&apos;s Revert&apos;s job
            instead.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4">
          <div className="grid gap-2">
            <Label htmlFor="reset-stage">Reset to</Label>
            <Select
              items={stages.map((s) => ({ value: s, label: STAGE_COPY[s].label }))}
              value={stage}
              onValueChange={(v) => {
                if (typeof v === "string") setStage(v as Stage);
              }}
            >
              <SelectTrigger id="reset-stage" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {stages.map((s) => (
                  <SelectItem key={s} value={s}>
                    {STAGE_COPY[s].label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-sm text-muted-foreground">
              {STAGE_COPY[stage].discards}
              {cascade}
            </p>
          </div>

          {stage === "elaboration" && (
            <>
              <div className="grid gap-2">
                <Label htmlFor="reset-destination">Land it on</Label>
                <Select
                  items={[
                    { value: "draft", label: "Draft — also un-curate it" },
                    { value: "ready", label: "Ready — stays curated" },
                  ]}
                  value={destination}
                  onValueChange={(v) => {
                    if (typeof v === "string")
                      setDestination(v as "draft" | "ready");
                  }}
                >
                  <SelectTrigger id="reset-destination" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="draft">
                      Draft — also un-curate it
                    </SelectItem>
                    <SelectItem value="ready">
                      Ready — stays curated
                    </SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="grid gap-2">
                <Label htmlFor="reset-note">
                  What should change? (optional)
                </Label>
                <Textarea
                  id="reset-note"
                  rows={3}
                  placeholder="Read for the API's auth handling before rewriting it."
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                />
              </div>
            </>
          )}

          <div className="grid gap-1.5">
            <p className="text-sm text-muted-foreground">
              Type <span className="font-mono font-medium">reset</span> to
              confirm:
            </p>
            <Input
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              placeholder="reset"
              autoComplete="off"
              spellCheck={false}
            />
          </div>

          {error && (
            <p className="text-sm font-medium text-destructive">{error}</p>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => reset(false)} disabled={busy}>
            Cancel
          </Button>
          <Button variant="destructive" onClick={confirm} disabled={busy || blocked}>
            {busy && <Loader2 className="size-4 animate-spin" />}
            Reset
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
