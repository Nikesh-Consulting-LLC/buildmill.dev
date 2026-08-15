"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Loader2, Sparkles } from "lucide-react";
import { apiCall, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { toastError, toastSuccess } from "@/components/ui/toast";

type Scope = "all" | "document";

/** US-43.2: put an agent on writing this project's guidelines.
 *
 * A dialog rather than a straight dispatch, because the two knobs change what
 * comes back enough to be worth a moment: what the agent may propose, and
 * where to point it. Confirm creates the chore and queues the run in one call
 * — dispatch_issue cannot produce a `guidelines` run, so there is no
 * create-now-dispatch-later path to leave a manager stranded on. */
export function RefreshGuidelinesDialog({
  projectId,
  hasRepo,
}: {
  projectId: string;
  hasRepo: boolean;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [scope, setScope] = useState<Scope>("all");
  const [focus, setFocus] = useState("");
  const [busy, setBusy] = useState(false);

  async function start() {
    setBusy(true);
    try {
      await apiCall(`/api/v1/projects/${projectId}/guidelines/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scope, focus }),
      });
      setOpen(false);
      // US-43.6: there is no work item to navigate to any more. The manager
      // stays where they are; the refresh announces itself on Things to Do
      // when the agent hands it back.
      toastSuccess(
        "An agent will read the repository and propose your guidelines"
      );
      router.refresh();
    } catch (e) {
      // A refresh already open is not an error the manager can do anything
      // about from here — send them to the one that is waiting instead of
      // making them find it. The 409's detail is structured for exactly this,
      // which is why ApiError keeps `detail` beside the stringified message.
      const detail =
        e instanceof ApiError && e.detail && typeof e.detail === "object"
          ? (e.detail as { refresh_id?: string; error?: string })
          : null;
      if (detail?.refresh_id) {
        setOpen(false);
        router.push(
          `/projects/${projectId}/guidelines/refresh/${detail.refresh_id}`
        );
        return;
      }
      toastError(
        detail?.error ??
          (e instanceof Error ? e.message : "Could not start the refresh")
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={!hasRepo}
        title={
          hasRepo
            ? "Put an agent on reading the source and writing the guidelines"
            : "Link a repository first — a refresh reads the source"
        }
        onClick={() => setOpen(true)}
      >
        <Sparkles className="size-4" />
        Refresh instructions
      </Button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Refresh instructions</DialogTitle>
            <DialogDescription>
              An agent studies this project&apos;s repository and delivery
              history and proposes revised instruction files — the Agent
              Instructions (AGENTS.md) and the per-task files under
              .buildmill/. Nothing is applied automatically — you read a diff
              per file and accept or reject the pass whole; nothing reaches
              the repository until you publish.
            </DialogDescription>
          </DialogHeader>

          <div className="grid gap-4">
            <div className="grid gap-2">
              <Label>What it may propose</Label>
              <div className="grid gap-2">
                {(
                  [
                    {
                      value: "all" as const,
                      title: "Agent Instructions and the per-task files",
                      hint: "AGENTS.md plus any .buildmill/*.md the repository gives a reason to change.",
                    },
                    {
                      value: "document" as const,
                      title: "Agent Instructions only",
                      hint: "The AGENTS.md body — leave the per-task instruction files alone.",
                    },
                  ] satisfies { value: Scope; title: string; hint: string }[]
                ).map((opt) => (
                  <button
                    key={opt.value}
                    type="button"
                    onClick={() => setScope(opt.value)}
                    className={`rounded-lg border p-3 text-left transition-colors ${
                      scope === opt.value
                        ? "border-primary bg-primary/5"
                        : "hover:bg-muted/50"
                    }`}
                  >
                    <p className="text-sm font-medium">{opt.title}</p>
                    <p className="text-xs text-muted-foreground">{opt.hint}</p>
                  </button>
                ))}
              </div>
            </div>

            <div className="grid gap-2">
              <Label htmlFor="refresh-focus">Focus (optional)</Label>
              <Textarea
                id="refresh-focus"
                rows={3}
                value={focus}
                onChange={(e) => setFocus(e.target.value)}
                placeholder="e.g. the API package is the part I care about"
              />
              <p className="text-xs text-muted-foreground">
                Carried into the agent&apos;s instructions. Leave it empty to
                let it read the whole repository.
              </p>
            </div>
          </div>

          <DialogFooter>
            <Button
              type="button"
              variant="ghost"
              onClick={() => setOpen(false)}
              disabled={busy}
            >
              Cancel
            </Button>
            <Button type="button" onClick={start} disabled={busy}>
              {busy ? <Loader2 className="size-4 animate-spin" /> : null}
              Start the refresh
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
