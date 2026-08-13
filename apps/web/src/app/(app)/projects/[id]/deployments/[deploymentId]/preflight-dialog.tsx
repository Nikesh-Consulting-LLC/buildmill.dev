"use client";

import { useState } from "react";
import { CheckCircle2, ListChecks, Loader2, XCircle } from "lucide-react";
import { apiCall } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";

type CheckResult = { check: string; ok: boolean; detail: string };

/** US-1.38: run preflight alone — no transfer, no script — to validate a
 * deployment config before its first real run. */
export function PreflightDialog({ deploymentId }: { deploymentId: string }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState<CheckResult[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleCheck() {
    setBusy(true);
    setError(null);
    setResults(null);
    try {
      const resp = (await apiCall(`/api/v1/deployments/${deploymentId}/preflight`, {
        method: "POST",
      })) as { ok: boolean; results: CheckResult[] };
      setResults(resp.results);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (o && !busy) handleCheck();
      }}
    >
      <DialogTrigger render={<Button variant="outline" size="sm" />}>
        <ListChecks className="size-3.5" />
        Check
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Preflight checks</DialogTitle>
          <DialogDescription>
            The same fast checks every run starts with — connection, target
            folder, disk space, tooling — without transferring anything.
          </DialogDescription>
        </DialogHeader>
        {busy && (
          <p className="inline-flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" />
            Checking the server…
          </p>
        )}
        {error && <p className="text-sm font-medium text-destructive">{error}</p>}
        {results && (
          <ul className="grid gap-1.5">
            {results.map((r) => (
              <li key={r.check} className="flex items-start gap-2 text-sm">
                {r.ok ? (
                  <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-600" />
                ) : (
                  <XCircle className="mt-0.5 size-4 shrink-0 text-destructive" />
                )}
                <span>
                  <span className="font-medium">{r.check}</span>{" "}
                  <span className="text-muted-foreground">{r.detail}</span>
                </span>
              </li>
            ))}
          </ul>
        )}
        {results && (
          <Button variant="outline" size="sm" onClick={handleCheck} disabled={busy}>
            Re-run checks
          </Button>
        )}
      </DialogContent>
    </Dialog>
  );
}
