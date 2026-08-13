"use client";

import { useState } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";

export function ConfirmDialog({
  trigger,
  title,
  description,
  confirmLabel,
  requireText,
  onConfirm,
  onForceConfirm,
  forceConfirmLabel = "Force delete anyway",
}: {
  trigger: React.ReactNode;
  title: string;
  description: string;
  confirmLabel: string;
  /** US-1.41: demand this exact text be typed before confirm enables. */
  requireText?: string;
  onConfirm: () => Promise<void>;
  /** When the normal confirm fails (e.g. active-work guard), offer this as a
   * second, more destructive action instead of a dead end. Only shown once
   * onConfirm has actually failed. */
  onForceConfirm?: () => Promise<void>;
  forceConfirmLabel?: string;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [typed, setTyped] = useState("");

  const blocked = !!requireText && typed !== requireText;

  async function handleConfirm() {
    setError(null);
    setBusy(true);
    try {
      await onConfirm();
      setOpen(false);
      setTyped("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function handleForceConfirm() {
    if (!onForceConfirm) return;
    setError(null);
    setBusy(true);
    try {
      await onForceConfirm();
      setOpen(false);
      setTyped("");
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
        if (!o) setTyped("");
      }}
    >
      <DialogTrigger render={trigger as React.ReactElement} />
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        {requireText && (
          <div className="grid gap-1.5">
            <p className="text-sm text-muted-foreground">
              Type <span className="font-mono font-medium">{requireText}</span> to
              confirm:
            </p>
            <Input
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              placeholder={requireText}
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
          {error && onForceConfirm && (
            <Button
              variant="outline"
              className="border-destructive text-destructive hover:bg-destructive/10"
              onClick={handleForceConfirm}
              disabled={busy || blocked}
            >
              {busy && <Loader2 className="size-4 animate-spin" />}
              {forceConfirmLabel}
            </Button>
          )}
          <Button
            variant="destructive"
            onClick={handleConfirm}
            disabled={busy || blocked}
          >
            {busy && <Loader2 className="size-4 animate-spin" />}
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
