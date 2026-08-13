"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "@/lib/router-with-progress";
import { Loader2, Plus } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
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

export type EpicBlocker = { id: string; title: string; displayId: string | null };

/** US-7.10: start a new epic. Only allowed once the active epic is wrapped up
 * (no open work items). Blockers are named with links so the manager can
 * finish, abandon, or delete each. The gate is enforced server-side too. */
export function StartNewEpicButton({
  projectId,
  activeEpicNumber,
  blockers,
}: {
  projectId: string;
  activeEpicNumber: number;
  blockers: EpicBlocker[];
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const gated = blockers.length > 0;

  async function start() {
    setSaving(true);
    setError(null);
    const supabase = createClient();
    const { error: rpcError } = await supabase.rpc("start_new_epic", {
      p_project: projectId,
    });
    setSaving(false);
    if (rpcError) {
      setError(rpcError.message);
      return;
    }
    setOpen(false);
    router.refresh();
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button variant="outline" size="sm" />}>
        <Plus className="size-4" />
        Start new epic
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Start Epic {activeEpicNumber + 1}</DialogTitle>
          <DialogDescription>
            Closes Epic {activeEpicNumber} and makes Epic {activeEpicNumber + 1}{" "}
            the active one — new work items land there. You can only start a new
            epic once every item in the current one is completed, deployed,
            abandoned, or deleted.
          </DialogDescription>
        </DialogHeader>
        {gated ? (
          <div className="grid gap-2 text-sm">
            <p className="font-medium">
              {blockers.length} work item{blockers.length === 1 ? "" : "s"} still
              open — finish, abandon, or delete each first:
            </p>
            <ul className="grid max-h-64 gap-1 overflow-y-auto">
              {blockers.map((b) => (
                <li key={b.id}>
                  <Link
                    href={`/issues/${b.id}`}
                    className="inline-flex items-center gap-2 rounded-md border px-2 py-1 underline-offset-4 hover:border-ring/60"
                  >
                    {b.displayId && (
                      <span className="font-mono text-xs text-muted-foreground">
                        {b.displayId}
                      </span>
                    )}
                    <span className="truncate">{b.title}</span>
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            Epic {activeEpicNumber} is fully wrapped up. Ready to start the next
            chapter.
          </p>
        )}
        {error && <p className="text-sm font-medium text-destructive">{error}</p>}
        <DialogFooter>
          <Button
            type="button"
            onClick={start}
            disabled={gated || saving}
            title={gated ? "Wrap up the current epic first" : undefined}
          >
            {saving && <Loader2 className="size-4 animate-spin" />}
            Start Epic {activeEpicNumber + 1}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
