"use client";

import { useMemo } from "react";
import { useRouter as useNextRouter } from "next/navigation";
import { runWithGlobalProgress } from "@/components/global-progress-bar";

// Drop-in replacement for next/navigation's useRouter: same object, same
// methods, but push/replace/refresh/back/forward run inside the global
// progress transition (see global-progress-bar.tsx) so every navigation and
// every router.refresh() shows the top progress bar — not just the ones a
// component author remembered to wire a local spinner for.
//
// `refreshSilently()` is the deliberate exception: background refreshes the
// user did not ask for — realtime subscriptions, polling — must not light the
// bar, or a page that streams updates flickers a "working…" signal forever and
// the bar stops meaning "your click landed".
export function useRouter() {
  const router = useNextRouter();

  return useMemo(
    () => ({
      ...router,
      push: (...args: Parameters<typeof router.push>) =>
        runWithGlobalProgress(() => router.push(...args)),
      replace: (...args: Parameters<typeof router.replace>) =>
        runWithGlobalProgress(() => router.replace(...args)),
      refresh: () => runWithGlobalProgress(() => router.refresh()),
      refreshSilently: () => router.refresh(),
      back: () => runWithGlobalProgress(() => router.back()),
      forward: () => runWithGlobalProgress(() => router.forward()),
    }),
    [router]
  );
}
