"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Loader2, Rocket, ShieldCheck, ThumbsDown, RotateCcw } from "lucide-react";
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
import { StopReleaseButton, STOPPABLE } from "../stop-release-button";
import { RetryReleaseButton } from "../retry-release-button";

/** US-21.5/21.6: the actions a release's state allows.
 *
 * Every one of them is DISABLED with its reason rather than hidden — a
 * missing button reads as "nothing to do", which is exactly wrong when the
 * truth is "three test cases still have no result". */
export function ReleaseActions({
  releaseId,
  version,
  status,
  signoffBlocker,
}: {
  releaseId: string;
  version: string;
  status: string;
  signoffBlocker: string | null;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function post(path: string, body?: unknown) {
    setBusy(path);
    setError(null);
    setNotice(null);
    try {
      const r = (await apiFetch(`/api/v1/releases/${releaseId}${path}`, {
        method: "POST",
        ...(body
          ? {
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(body),
            }
          : {}),
      })) as { mode?: string; reason?: string; deployment?: string };
      if (r.mode === "direct") {
        setNotice(
          `${r.reason} — running ${r.deployment} from here instead. ` +
            "Confirm once it finishes."
        );
      }
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        {/* US-23.1, widened by us-103.3: every state a Stop can end. It used
            to render for `queued` alone, so a release whose agent had died
            offered the manager nothing at all. */}
        {STOPPABLE.has(status) && (
          <StopReleaseButton
            releaseId={releaseId}
            version={version}
            status={status}
          />
        )}

        {/* US-90.1: the attempt failed before anything shipped — retry the
            failed leg on the same version and pinned commit. A rejected
            build (UAT proved it bad) never shows this. */}
        {(status === "failed" || status === "uat-deploy-failed") && (
          <RetryReleaseButton releaseId={releaseId} />
        )}

        {status === "uat-deployed" && (
          <>
            <Button
              size="sm"
              disabled={!!busy || !!signoffBlocker}
              title={signoffBlocker ?? "Every test case passed — sign it off"}
              onClick={() => post("/sign-off")}
            >
              {busy === "/sign-off" ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <ShieldCheck className="size-4" />
              )}
              Sign off UAT
            </Button>
            <RejectDialog releaseId={releaseId} onDone={() => router.refresh()} />
          </>
        )}

        {status === "uat-signed-off" && (
          <Button
            size="sm"
            disabled={!!busy}
            onClick={() => post("/promote")}
            title="Ships the pinned commit to Production — not the head of main"
          >
            {busy === "/promote" ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Rocket className="size-4" />
            )}
            Promote to Production
          </Button>
        )}

        {status === "promoting" && (
          <Button
            size="sm"
            disabled={!!busy}
            onClick={() => post("/confirm-released")}
            title="Checks that a production deployment of this exact commit succeeded"
          >
            {busy === "/confirm-released" ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <ShieldCheck className="size-4" />
            )}
            Confirm released
          </Button>
        )}

        {status === "released" && (
          <Button
            size="sm"
            variant="outline"
            disabled={!!busy}
            onClick={() => post("/rolled-back")}
            title="Record that this version was rolled back out of production"
          >
            {busy === "/rolled-back" ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <RotateCcw className="size-4" />
            )}
            Record rollback
          </Button>
        )}
      </div>

      {status === "uat-deployed" && signoffBlocker && (
        <p className="text-xs text-muted-foreground">{signoffBlocker}</p>
      )}
      {notice && <p className="text-xs text-amber-600">{notice}</p>}
      {error && (
        <p className="text-sm font-medium text-destructive">{error}</p>
      )}
    </div>
  );
}

/** A rejection needs a reason: the release is superseded, never re-run, so
 * the reason is the only record of why that version does not exist. */
function RejectDialog({
  releaseId,
  onDone,
}: {
  releaseId: string;
  onDone: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function reject() {
    setBusy(true);
    setError(null);
    try {
      await apiFetch(`/api/v1/releases/${releaseId}/reject`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ comment }),
      });
      setOpen(false);
      onDone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button variant="outline" size="sm" />}>
        <ThumbsDown className="size-4" />
        Reject
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Reject this release</DialogTitle>
          <DialogDescription>
            A release is immutable — this version will never be re-deployed.
            Fix the problem on the default branch and cut a new one, which
            supersedes this.
          </DialogDescription>
        </DialogHeader>
        <Textarea
          rows={4}
          placeholder="What failed?"
          value={comment}
          onChange={(e) => setComment(e.target.value)}
        />
        {error && (
          <p className="text-sm font-medium text-destructive">{error}</p>
        )}
        <DialogFooter>
          <Button disabled={busy || !comment.trim()} onClick={reject}>
            {busy && <Loader2 className="size-4 animate-spin" />}
            Reject release
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
