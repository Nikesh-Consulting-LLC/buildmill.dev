"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "@/lib/router-with-progress";
import {
  CheckCircle2,
  CircleDashed,
  Loader2,
  RotateCcw,
  ShieldAlert,
  ShieldOff,
  XCircle,
} from "lucide-react";
import { apiFetch } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
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

export type ReleaseSuiteRun = {
  id: string;
  suite_id: string;
  trigger: string;
  status: string;
  tests_total: number | null;
  tests_passed: number | null;
  tests_failed: number | null;
  waived_at: string | null;
  waive_reason: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
};

type SuiteRow = {
  id: string;
  name: string;
  layer: string;
  blocks_signoff: boolean;
};

function statusTone(status: string): string {
  if (status === "succeeded") return "text-emerald-600 dark:text-emerald-400";
  if (status === "queued" || status === "running")
    return "text-amber-600 dark:text-amber-400";
  return "text-destructive";
}

function WaiveDialog({
  releaseId,
  suiteId,
  suiteName,
  onDone,
}: {
  releaseId: string;
  suiteId: string;
  suiteName: string;
  onDone: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleWaive(e: React.FormEvent) {
    e.preventDefault();
    if (reason.trim().length < 3) {
      setError("A waiver needs a reason on the record.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await apiFetch(`/api/v1/releases/${releaseId}/suites/${suiteId}/waive`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: reason.trim() }),
      });
      setOpen(false);
      setReason("");
      onDone();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button variant="outline" size="xs" />}>
        <ShieldOff className="size-3" />
        Waive
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Waive {suiteName} for this release?</DialogTitle>
          <DialogDescription>
            Sign-off proceeds despite this verdict. The waiver stamps this run
            and is audited — a re-run produces a fresh, unwaived verdict.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleWaive} className="grid gap-3">
          <Input
            placeholder="Why is this safe to waive?"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
          {error && (
            <p className="text-sm font-medium text-destructive">{error}</p>
          )}
          <DialogFooter>
            <Button type="submit" variant="destructive" disabled={busy}>
              {busy && <Loader2 className="size-4 animate-spin" />}
              Waive this verdict
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function ReleaseSuites({
  releaseId,
  environment,
  releaseStatus,
  suites,
  runs,
}: {
  releaseId: string;
  environment: "uat" | "production";
  releaseStatus: string;
  suites: SuiteRow[];
  runs: ReleaseSuiteRun[];
}) {
  const router = useRouter();
  const [rerunning, setRerunning] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (!suites.length) return null;

  // runs come newest-first; the first per suite is its latest verdict.
  const latest = new Map<string, ReleaseSuiteRun>();
  for (const r of runs) {
    const wanted =
      environment === "uat" ? r.trigger !== "prod-promote" : r.trigger === "prod-promote";
    if (wanted && !latest.has(r.suite_id)) latest.set(r.suite_id, r);
  }

  const rerunnable =
    environment === "uat"
      ? releaseStatus === "uat-deployed"
      : releaseStatus === "released";

  const prodFailure =
    environment === "production" &&
    [...latest.values()].some(
      (r) => !["succeeded", "queued", "running"].includes(r.status)
    );

  async function rerun(suiteId: string) {
    setRerunning(suiteId);
    setError(null);
    try {
      await apiFetch(`/api/v1/releases/${releaseId}/suites/${suiteId}/rerun`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ environment }),
      });
      router.refresh();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setRerunning(null);
    }
  }

  return (
    <Card
      className={cn(prodFailure && "border-destructive/50 bg-destructive/5")}
    >
      <CardHeader>
        <CardTitle className="text-base">
          {environment === "uat" ? "Automated suites" : "Production smoke"}
        </CardTitle>
        <CardDescription>
          {environment === "uat"
            ? "Run automatically against the UAT deployment the moment this release lands there, pinned to its commit."
            : "The prod-safe subset, run against production after go-live."}
        </CardDescription>
        {prodFailure && (
          <p className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm font-medium text-destructive">
            <ShieldAlert className="mt-0.5 size-4 shrink-0" />
            A production smoke suite did not pass. Inspect the failing run
            below; if production is actually broken, roll back with “Mark
            rolled back” above — the release record stays honest either way.
          </p>
        )}
      </CardHeader>
      <CardContent>
        <ul className="grid gap-2">
          {suites.map((s) => {
            const run = latest.get(s.id);
            return (
              <li
                key={s.id}
                className="flex flex-wrap items-center gap-2 rounded-md border px-3 py-2 text-sm"
              >
                <span className="font-medium">{s.name}</span>
                <Badge variant="secondary">{s.layer}</Badge>
                {s.blocks_signoff ? (
                  <Badge variant="outline" title="A non-passing run blocks sign-off unless waived">
                    gates sign-off
                  </Badge>
                ) : (
                  <Badge
                    variant="outline"
                    className="text-muted-foreground"
                    title="Results are shown but never block sign-off"
                  >
                    advisory
                  </Badge>
                )}
                <span className="ml-auto flex items-center gap-2">
                  {!run ? (
                    <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                      <CircleDashed className="size-3.5" />
                      not run yet
                    </span>
                  ) : (
                    <>
                      <Link
                        href={`/tests/suites/${run.id}`}
                        className={cn(
                          "inline-flex items-center gap-1 text-xs font-medium underline-offset-4 hover:underline",
                          statusTone(run.status)
                        )}
                      >
                        {run.status === "succeeded" ? (
                          <CheckCircle2 className="size-3.5" />
                        ) : run.status === "queued" || run.status === "running" ? (
                          <Loader2 className="size-3.5 animate-spin" />
                        ) : (
                          <XCircle className="size-3.5" />
                        )}
                        {run.status}
                      </Link>
                      {run.tests_total !== null && (
                        <span className="text-xs text-muted-foreground">
                          {run.tests_passed}/{run.tests_total} passed
                        </span>
                      )}
                      {run.waived_at && (
                        <Badge variant="outline" title={run.waive_reason ?? undefined}>
                          waived
                        </Badge>
                      )}
                    </>
                  )}
                  {rerunnable && (
                    <Button
                      variant="outline"
                      size="xs"
                      disabled={
                        rerunning === s.id ||
                        run?.status === "queued" ||
                        run?.status === "running"
                      }
                      onClick={() => rerun(s.id)}
                      title="Run this suite again for this release"
                    >
                      {rerunning === s.id ? (
                        <Loader2 className="size-3 animate-spin" />
                      ) : (
                        <RotateCcw className="size-3" />
                      )}
                      Re-run
                    </Button>
                  )}
                  {rerunnable &&
                    environment === "uat" &&
                    s.blocks_signoff &&
                    run &&
                    !run.waived_at &&
                    !["succeeded", "queued", "running"].includes(run.status) && (
                      <WaiveDialog
                        releaseId={releaseId}
                        suiteId={s.id}
                        suiteName={s.name}
                        onDone={() => router.refresh()}
                      />
                    )}
                </span>
              </li>
            );
          })}
        </ul>
        {error && (
          <p className="mt-2 text-sm font-medium text-destructive">{error}</p>
        )}
      </CardContent>
    </Card>
  );
}
