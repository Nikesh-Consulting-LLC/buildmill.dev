"use client";

import { useEffect, useState } from "react";
import { GitPullRequestArrow, Loader2 } from "lucide-react";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

type Branch = { name: string; commit_sha: string };

/** US-1.50: deploy a different branch/commit once — configured branch
 * untouched, the next plain Run ships it again. */
export function RefOverrideDialog({
  deploymentId,
  repoFullName,
  configuredBranch,
  disabled,
  onStarted,
}: {
  deploymentId: string;
  repoFullName: string;
  configuredBranch: string;
  disabled?: boolean;
  onStarted: (runId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [branches, setBranches] = useState<Branch[] | null>(null);
  const [branch, setBranch] = useState("");
  const [sha, setSha] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || branches !== null) return;
    let cancelled = false;
    (async () => {
      try {
        const list = (await apiCall(
          `/api/v1/github/repos/${repoFullName}/branches`
        )) as Branch[];
        if (!cancelled) setBranches(list);
      } catch (e) {
        if (!cancelled && !(e instanceof ApiError && e.status === 404))
          setError((e as Error).message);
        if (!cancelled) setBranches([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, branches, repoFullName]);

  const ref = sha.trim() || branch;

  async function handleDeploy() {
    if (!ref) {
      setError("Pick a branch or paste a commit SHA.");
      return;
    }
    setError(null);
    setBusy(true);
    try {
      const resp = (await apiCall(`/api/v1/deployments/${deploymentId}/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ref }),
      })) as { run_id: string };
      setOpen(false);
      onStarted(resp.run_id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button variant="outline" size="sm" disabled={disabled} />}>
        <GitPullRequestArrow className="size-3.5" />
        Deploy a different ref…
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>One-off deploy of a different ref</DialogTitle>
          <DialogDescription>
            Ships the chosen branch or commit through the normal pipeline
            once. The configured branch (
            <span className="font-mono">{configuredBranch}</span>) is
            untouched — the next plain Run uses it again.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-3">
          <div className="grid gap-2">
            <Label htmlFor="ovr-branch">Branch</Label>
            {branches === null ? (
              <p className="inline-flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="size-3.5 animate-spin" />
                Loading branches…
              </p>
            ) : (
              <Select
                items={branches.map((b) => ({ value: b.name, label: b.name }))}
                value={branch || null}
                onValueChange={(v) => {
                  if (typeof v === "string") {
                    setBranch(v);
                    setSha("");
                  }
                }}
              >
                <SelectTrigger id="ovr-branch" className="w-full">
                  <SelectValue placeholder="Pick a branch" />
                </SelectTrigger>
                <SelectContent>
                  {branches.map((b) => (
                    <SelectItem key={b.name} value={b.name}>
                      {b.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </div>
          <div className="grid gap-2">
            <Label htmlFor="ovr-sha">…or a commit SHA</Label>
            <Input
              id="ovr-sha"
              placeholder="abc1234"
              className="font-mono"
              value={sha}
              onChange={(e) => setSha(e.target.value)}
            />
          </div>
          {ref && (
            <p className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300">
              Override: this run deploys{" "}
              <span className="font-mono font-medium">{ref}</span> instead of{" "}
              <span className="font-mono">{configuredBranch}</span>.
            </p>
          )}
        </div>

        {error && <p className="text-sm font-medium text-destructive">{error}</p>}
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={handleDeploy} disabled={busy || !ref}>
            {busy && <Loader2 className="size-4 animate-spin" />}
            Deploy override
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
