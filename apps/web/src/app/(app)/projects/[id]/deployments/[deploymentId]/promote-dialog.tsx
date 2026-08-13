"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { ArrowUpRight, Loader2 } from "lucide-react";
import { apiCall } from "@/lib/api";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export type SiblingDeployment = {
  id: string;
  name: string;
  target_folder: string;
  protected: boolean;
  serverLabel: string;
};

/** US-1.43: ship the exact tested payload to a sibling deployment. */
export function PromoteDialog({
  projectId,
  deploymentId,
  runId,
  payloadLabel,
  checksum,
  siblings,
}: {
  projectId: string;
  deploymentId: string;
  runId: string;
  payloadLabel: string;
  checksum: string | null;
  siblings: SiblingDeployment[];
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [targetId, setTargetId] = useState("");
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const target = siblings.find((s) => s.id === targetId);
  const typeBlocked = !!target?.protected && typed !== target.name;

  async function handlePromote() {
    if (!target) return;
    setError(null);
    setBusy(true);
    try {
      await apiCall(`/api/v1/deployments/${deploymentId}/runs/${runId}/promote`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target_deployment_id: target.id }),
      });
      setOpen(false);
      router.push(`/projects/${projectId}/deployments/${target.id}`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (siblings.length === 0) return null;

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button variant="ghost" size="sm" />}>
        <ArrowUpRight className="size-3.5" />
        Promote
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Promote this run</DialogTitle>
          <DialogDescription>
            Ships the <span className="font-medium">exact payload that was
            tested</span> — <span className="font-mono">{payloadLabel}</span>
            {checksum && (
              <span className="font-mono"> (sha256 {checksum.slice(0, 12)}…)</span>
            )}{" "}
            — through the target&apos;s full pipeline: its preflight, strategy,
            env vars, health check, and protection. The target&apos;s own
            source folder/excludes apply to re-fetched commits; archived and
            zip payloads ship as-is.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-2">
          <Label htmlFor="promote-target">Target deployment</Label>
          <Select
            items={siblings.map((s) => ({
              value: s.id,
              label: `${s.name} — ${s.serverLabel}:${s.target_folder}`,
            }))}
            value={targetId || null}
            onValueChange={(v) => {
              if (typeof v === "string") {
                setTargetId(v);
                setTyped("");
              }
            }}
          >
            <SelectTrigger id="promote-target" className="w-full">
              <SelectValue placeholder="Pick a deployment" />
            </SelectTrigger>
            <SelectContent>
              {siblings.map((s) => (
                <SelectItem key={s.id} value={s.id}>
                  {s.name} — {s.serverLabel}:{s.target_folder}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {target?.protected && (
          <div className="grid gap-1.5 rounded-md border border-destructive/40 bg-destructive/5 p-3">
            <p className="text-sm font-medium text-destructive">
              &quot;{target.name}&quot; is protected — type its name to confirm:
            </p>
            <input
              className="h-8 rounded-md border bg-background px-2 font-mono text-sm"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              placeholder={target.name}
              autoComplete="off"
              spellCheck={false}
            />
          </div>
        )}

        {error && <p className="text-sm font-medium text-destructive">{error}</p>}
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)} disabled={busy}>
            Cancel
          </Button>
          <Button
            variant={target?.protected ? "destructive" : "default"}
            onClick={handlePromote}
            disabled={busy || !target || typeBlocked}
          >
            {busy && <Loader2 className="size-4 animate-spin" />}
            Promote
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
