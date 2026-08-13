"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, Info, X, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";

// US-6.2: a minimal, dependency-free toast. Transient confirmations for
// actions taken away from their full surface (inline dispatch, batch outcome).
// A module-level store lets any client component call `toast(...)`; a single
// <Toaster /> mounted in the app shell renders the stack.

export type ToastVariant = "success" | "error" | "info";

type ToastItem = {
  id: number;
  title: string;
  description?: string;
  variant: ToastVariant;
};

let items: ToastItem[] = [];
let nextId = 1;
const listeners = new Set<(next: ToastItem[]) => void>();

function emit() {
  for (const l of listeners) l(items);
}

function dismiss(id: number) {
  items = items.filter((t) => t.id !== id);
  emit();
}

export function toast(input: {
  title: string;
  description?: string;
  variant?: ToastVariant;
}) {
  const item: ToastItem = {
    id: nextId++,
    title: input.title,
    description: input.description,
    variant: input.variant ?? "info",
  };
  items = [...items, item];
  emit();
  // Errors linger a little longer since they carry a reason to read.
  const ttl = item.variant === "error" ? 7000 : 4000;
  setTimeout(() => dismiss(item.id), ttl);
  return item.id;
}

export const toastSuccess = (title: string, description?: string) =>
  toast({ title, description, variant: "success" });
export const toastError = (title: string, description?: string) =>
  toast({ title, description, variant: "error" });

const ICONS = {
  success: CheckCircle2,
  error: XCircle,
  info: Info,
} as const;

const ACCENT = {
  success: "text-emerald-600 dark:text-emerald-400",
  error: "text-destructive",
  info: "text-muted-foreground",
} as const;

export function Toaster() {
  // Seeded from the store; a toast can only fire after mount (from a user
  // action), so subscribing in the effect misses nothing.
  const [list, setList] = useState<ToastItem[]>(() => items);

  useEffect(() => {
    listeners.add(setList);
    return () => {
      listeners.delete(setList);
    };
  }, []);

  if (!list.length) return null;

  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-0 z-[100] flex flex-col items-center gap-2 p-4 sm:items-end">
      {list.map((t) => {
        const Icon = ICONS[t.variant];
        return (
          <div
            key={t.id}
            role="status"
            aria-live="polite"
            className={cn(
              "pointer-events-auto flex w-full max-w-sm items-start gap-3 rounded-lg border bg-popover p-3 text-popover-foreground shadow-lg",
              "motion-safe:animate-in motion-safe:fade-in motion-safe:slide-in-from-bottom-2"
            )}
          >
            <Icon className={cn("mt-0.5 size-4 shrink-0", ACCENT[t.variant])} />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium">{t.title}</p>
              {t.description && (
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {t.description}
                </p>
              )}
            </div>
            <button
              type="button"
              onClick={() => dismiss(t.id)}
              aria-label="Dismiss"
              className="-m-1 shrink-0 rounded-md p-1 text-muted-foreground transition-colors hover:text-foreground"
            >
              <X className="size-3.5" />
            </button>
          </div>
        );
      })}
    </div>
  );
}
