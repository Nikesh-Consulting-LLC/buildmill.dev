"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

// A single, center-aligned, in-app confirmation modal shared by the whole app,
// replacing native window.confirm(). Mirrors the toast store pattern: a
// module-level `confirmDialog(...)` any client component can call (returns a
// Promise<boolean>), rendered by one <ConfirmDialog /> mounted in the shell.

export type ConfirmOptions = {
  title: string;
  description?: React.ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  /** Red confirm button for irreversible / dangerous actions. */
  destructive?: boolean;
};

type Pending = ConfirmOptions & { id: number; resolve: (ok: boolean) => void };

let current: Pending | null = null;
let nextId = 1;
const listeners = new Set<(p: Pending | null) => void>();

function emit() {
  for (const l of listeners) l(current);
}

/** Ask the user to confirm; resolves true on confirm, false on cancel/dismiss. */
export function confirmDialog(opts: ConfirmOptions): Promise<boolean> {
  // Only one at a time — abandon any in-flight request as cancelled.
  if (current) current.resolve(false);
  return new Promise<boolean>((resolve) => {
    current = { ...opts, id: nextId++, resolve };
    emit();
  });
}

function settle(ok: boolean) {
  if (current) current.resolve(ok);
  current = null;
  emit();
}

export function ConfirmDialog() {
  const [pending, setPending] = useState<Pending | null>(() => current);
  // Retain the last content while the close animation plays, so the dialog
  // never flashes empty as it dismisses.
  const [shown, setShown] = useState<Pending | null>(pending);

  useEffect(() => {
    const listener = (p: Pending | null) => {
      setPending(p);
      if (p) setShown(p);
    };
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  }, []);

  return (
    <Dialog
      open={!!pending}
      onOpenChange={(open) => {
        if (!open) settle(false);
      }}
    >
      <DialogContent showCloseButton={false} className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{shown?.title}</DialogTitle>
          {shown?.description != null && (
            <DialogDescription>{shown.description}</DialogDescription>
          )}
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => settle(false)}>
            {shown?.cancelLabel ?? "Cancel"}
          </Button>
          <Button
            variant={shown?.destructive ? "destructive" : "default"}
            onClick={() => settle(true)}
          >
            {shown?.confirmLabel ?? "Confirm"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
