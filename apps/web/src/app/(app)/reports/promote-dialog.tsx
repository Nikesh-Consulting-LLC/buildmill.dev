"use client";

import { useState } from "react";
import Link from "next/link";
import { ArrowUpRight } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import type { ReportRow } from "./report-types";

export type PromoteEpic = { id: string; project_id: string; title: string; number: number };

/**
 * US-16.7: the hinge. One action turns a report into a normal `bug` work item
 * — everything the manager is looking at is already in the description, so
 * this asks only the one thing the report cannot answer: which epic, if any.
 *
 * The RPC does the whole transition in one transaction and refuses a second
 * promotion, so this dialog does not have to guard against a double click
 * minting two work items.
 */
export function PromoteDialog({
  report,
  epics,
  onPromoted,
}: {
  report: ReportRow;
  epics: PromoteEpic[];
  onPromoted: (issueId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [epicId, setEpicId] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<string | null>(null);

  const projectEpics = epics.filter((e) => e.project_id === report.project_id);

  async function promote() {
    setBusy(true);
    setError(null);
    const { data, error: rpcError } = await createClient().rpc("promote_app_issue", {
      p_app_issue: report.id,
      p_epic_id: epicId || undefined,
    });
    setBusy(false);
    if (rpcError) {
      setError(rpcError.message);
      return;
    }
    if (typeof data === "string") {
      setCreated(data);
      onPromoted(data);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button size="sm" />}>
        Promote to work item
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Promote this report</DialogTitle>
          <DialogDescription>
            Creates a <strong>bug</strong> work item with the title, description
            and stack trace already filled in. From that point it is an ordinary
            work item — plan, code, review, no special handling.
          </DialogDescription>
        </DialogHeader>

        {created ? (
          <div className="grid gap-3 text-sm">
            <p>Promoted. The work item is ready to plan.</p>
            <Link
              href={`/issues/${created}?from=${encodeURIComponent("/reports")}&fromLabel=${encodeURIComponent("Reports")}`}
              className="inline-flex items-center gap-1 font-medium text-primary hover:underline"
            >
              Open the work item <ArrowUpRight className="size-4" />
            </Link>
          </div>
        ) : (
          <div className="grid gap-3">
            <p className="rounded-md border bg-muted px-3 py-2 text-sm">
              {report.title}
            </p>
            <label className="grid gap-1 text-sm">
              <span className="text-muted-foreground">
                Epic (optional — a standalone bug does not need one)
              </span>
              <select
                className="rounded-md border bg-background px-3 py-2 text-sm"
                value={epicId}
                onChange={(e) => setEpicId(e.target.value)}
              >
                <option value="">No epic</option>
                {projectEpics.map((e) => (
                  <option key={e.id} value={e.id}>
                    #{e.number} · {e.title}
                  </option>
                ))}
              </select>
            </label>
            {error && (
              <p className="text-sm font-medium text-destructive">{error}</p>
            )}
          </div>
        )}

        <DialogFooter>
          <DialogClose render={<Button variant="outline" />}>
            {created ? "Done" : "Cancel"}
          </DialogClose>
          {!created && (
            <Button onClick={promote} disabled={busy}>
              {busy ? "Promoting…" : "Promote"}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
