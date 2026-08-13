"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Copy, Loader2 } from "lucide-react";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/** US-1.42: create a sibling (staging -> production) without re-entering
 * the config. Env var values are copied server-side; protected is not. */
export function DuplicateDialog({
  deploymentId,
  sourceName,
}: {
  deploymentId: string;
  sourceName: string;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(`Copy of ${sourceName}`);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleDuplicate(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await apiCall(`/api/v1/deployments/${deploymentId}/duplicate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim() }),
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
      <DialogTrigger render={<Button variant="ghost" size="sm" />}>
        <Copy className="size-3.5" />
        Duplicate
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Duplicate &quot;{sourceName}&quot;</DialogTitle>
          <DialogDescription>
            Copies the full configuration — server, branch, folders, script,
            strategy, health check. Environment variable values will be copied
            (server-side, never shown). The duplicate starts unprotected, with
            no run history, and nothing runs automatically.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleDuplicate} className="grid gap-3">
          <div className="grid gap-2">
            <Label htmlFor="dup-name">New name</Label>
            <Input
              id="dup-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          {error && <p className="text-sm font-medium text-destructive">{error}</p>}
          <DialogFooter>
            <Button type="submit" disabled={busy || !name.trim()}>
              {busy && <Loader2 className="size-4 animate-spin" />}
              Duplicate
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
