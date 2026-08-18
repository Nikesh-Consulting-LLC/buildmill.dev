"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Loader2, OctagonX } from "lucide-react";
import { apiFetch } from "@/lib/api";
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
import { Textarea } from "@/components/ui/textarea";

/** US-23.1, widened by US-103.3: stop a release that is going nowhere.
 *
 * This used to render only for a `queued` release, because the API refused
 * everything else — it reasoned that once an agent held the prep the honest
 * routes were "stop the run, or let it reach UAT and reject it". But release
 * prep is not a `runs` row: there was no Stop-work button pointed at it
 * anywhere, and rejecting a release stuck at `running` left the prep alive.
 * The escape hatch it pointed at did not exist, which is how release
 * 2026.08.16.3 came to be cleared by editing the production database.
 *
 * Stop is a verdict on the ATTEMPT — the agent died, the job hung, I changed
 * my mind. Reject is a verdict on the BUILD, and burns the version forever.
 * The copy here keeps them apart on purpose. */
export function StopReleaseButton({
  releaseId,
  version,
  status,
  size = "sm",
}: {
  releaseId: string;
  version: string;
  status?: string;
  size?: "sm" | "icon-sm";
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);

  // An agent is holding the job in these two; the wording should say so,
  // because stopping then ends a session that is (or looked) live.
  const held = status === "running" || status === "notes-ready";
  // US-120.1: at `deploying` the job is the UAT deploy pipeline. Stopping
  // cancels it — cooperatively if it is live — and US-1.35's rule applies:
  // what already reached the server is not undone. Said here, before the
  // click, because that is the one thing the manager cannot see afterwards.
  const deploying = status === "deploying";

  async function stop() {
    setBusy(true);
    setError(null);
    try {
      await apiFetch(`/api/v1/releases/${releaseId}/cancel`, {
        method: "POST",
        body: JSON.stringify({ comment: reason.trim() || null }),
      });
      setOpen(false);
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button variant="outline" size={size} />}>
        <OctagonX className="size-4" />
        {size === "sm" ? "Stop" : ""}
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Stop release {version}?</DialogTitle>
          <DialogDescription>
            {deploying
              ? "The UAT deploy is cancelled. Files already transferred and script steps already run on the UAT server are not undone. "
              : held
                ? "The agent preparing it is released from the job, and anything it hands back afterwards is refused. "
                : "Its queued job is removed. "}
            The release is marked stopped and the project is freed immediately,
            so you can cut a replacement at once. The version {version} is not
            reused — a version names exactly one build — and the git tag is left
            alone.
          </DialogDescription>
        </DialogHeader>
        {/* The distinction that matters, said once, where the decision is. */}
        <p className="rounded-md bg-muted p-3 text-xs text-muted-foreground">
          Stop says <strong>this attempt</strong> failed — nothing was learned
          about the build. If testing found the build itself bad, reject it
          instead: that is a verdict on the build and it is final.
          {deploying && (
            <>
              {" "}
              To retry the deploy on this same version instead, cancel the run
              on the deployment page — the release then reads{" "}
              <em>UAT deploy failed</em> and offers Retry.
            </>
          )}
        </p>
        <Textarea
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="Why (optional) — e.g. the runner restarted mid-job"
          rows={2}
        />
        {error && (
          <p className="text-sm font-medium text-destructive">{error}</p>
        )}
        <DialogFooter>
          <Button variant="outline" disabled={busy} onClick={() => setOpen(false)}>
            Keep it
          </Button>
          <Button disabled={busy} onClick={stop}>
            {busy && <Loader2 className="size-4 animate-spin" />}
            Stop release
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
