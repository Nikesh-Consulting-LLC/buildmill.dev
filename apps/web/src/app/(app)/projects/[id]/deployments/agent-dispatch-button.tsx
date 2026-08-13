"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Bot, Loader2 } from "lucide-react";
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

/** US-13.13: hand this deployment's execution and babysitting to an
 * agent — a `deploy` pool run for workers granted the deploy
 * capability. The human decision moves to dispatch time: the manager
 * picks the moment and pre-authorizes (or not) the one allowed
 * auto-rollback. Protected deployments never see this button;
 * production needs the audited "agent may deploy" flag. */
export function AgentDispatchButton({
  deploymentId,
  environment,
  agentDispatchAllowed,
  isProtected,
  externalTargetBranch,
}: {
  deploymentId: string;
  environment: string | null;
  agentDispatchAllowed: boolean;
  isProtected: boolean;
  /** US-50.3: dispatch stays available on an external deployment with its
   * rails intact — what changes is the contract. There is no health step and
   * nothing to roll back, so the pre-authorization is not offered. */
  externalTargetBranch?: string | null;
}) {
  const external = !!externalTargetBranch;
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [autoRollback, setAutoRollback] = useState(false);
  const [ref, setRef] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (isProtected) return null; // human-only, always — no dead control
  const blocked = environment === "production" && !agentDispatchAllowed;

  async function dispatch() {
    setBusy(true);
    setError(null);
    try {
      await apiCall(`/api/v1/deployments/${deploymentId}/agent-dispatch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ref: ref.trim() || null,
          auto_rollback: external ? false : autoRollback,
        }),
      });
      setOpen(false);
      router.refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : (e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger
        render={
          <Button
            variant="outline"
            size="sm"
            disabled={blocked}
            title={
              blocked
                ? "Production needs the 'agent may deploy' flag on this deployment (set it in the deployment settings — the flip is audited)."
                : "Dispatch this deployment to an agent granted the deploy capability"
            }
          >
            <Bot className="size-3.5" />
            Dispatch to agent
          </Button>
        }
      />
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Dispatch this deployment to an agent</DialogTitle>
          <DialogDescription>
            {external ? (
              <>
                A worker granted the <code>deploy</code> capability triggers
                the merge into{" "}
                <span className="font-mono">{externalTargetBranch}</span>,
                watches it land, and reports a verdict. There is no health
                check to run and nothing to roll back — the merge landing is
                the whole verdict, and what your pipeline does next is not
                something the agent can claim.
              </>
            ) : (
              <>
                A worker granted the <code>deploy</code> capability triggers
                the existing deployment machinery, watches it land, verifies
                the health checks, and reports a verdict. It never sees
                credentials — the server executes, the agent orchestrates.
              </>
            )}
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          {!external && (
            <label className="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={autoRollback}
                onChange={(e) => setAutoRollback(e.target.checked)}
              />
              <span>
                <span className="font-medium">Pre-authorize auto-rollback</span>
                <span className="block text-xs text-muted-foreground">
                  On failed health checks the agent may trigger the existing
                  rollback exactly once. Without this, it reports
                  deployed-but-unhealthy and stops — the agent never rolls back
                  on its own authority.
                </span>
              </span>
            </label>
          )}
          <label className="grid gap-1 text-sm">
            <span className="text-muted-foreground">
              Ref override (optional — as for a manual run)
            </span>
            <input
              value={ref}
              onChange={(e) => setRef(e.target.value)}
              placeholder="branch, tag, or commit — empty deploys the configured branch"
              className="rounded-md border px-2 py-1 font-mono text-xs"
            />
          </label>
          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" size="sm" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button size="sm" disabled={busy} onClick={dispatch}>
            {busy && <Loader2 className="size-3.5 animate-spin" />}
            Dispatch
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
