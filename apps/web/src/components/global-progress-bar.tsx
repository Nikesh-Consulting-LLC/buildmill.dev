"use client";

import { useEffect, useRef, useState, useTransition } from "react";
import { cn } from "@/lib/utils";
import {
  PROGRESS_FADE_MS,
  remainingHoldMs,
} from "@/lib/progress-timing";

// A global "the app is working" signal: any router.push/replace/refresh call
// made through `useRouter` in "@/lib/router-with-progress" runs inside this
// component's own transition, so `isPending` reflects every in-flight
// navigation or router.refresh() across the app, not just the one dialog
// that triggered it. This is what closes the gap `router.refresh()` left —
// previously there was zero feedback once a dialog closed and the page was
// still re-fetching, which read as "did my click even register?" and
// invited a second click.
//
// US-87.11: Phase 87 made navigation fast enough that this stopped working.
// The old visual was a 1.1s sweep starting at translateX(-100%) — fully
// off-screen left — behind a 150ms opacity fade. A 150ms navigation showed
// the bar reaching full opacity around 14% of its travel, still mostly off
// screen, and then fading out: nothing to see. Two changes fix it.
//
// 1. An indeterminate FILL rather than a sweep. A fill reads correctly at any
//    duration — at 400ms you are already halfway across — where a sweep only
//    reads if you watch the whole cycle.
// 2. A minimum visible duration (see lib/progress-timing.ts) instead of the
//    conventional delay-before-showing. A delay would suppress the signal on
//    exactly the fast operations that felt unacknowledged.
let runInGlobalTransition: ((callback: () => void) => void) | null = null;

export function runWithGlobalProgress(callback: () => void) {
  if (runInGlobalTransition) runInGlobalTransition(callback);
  else callback();
}

type Phase = "idle" | "loading" | "done";

export function GlobalProgressBar() {
  const [isPending, startTransition] = useTransition();
  const [phase, setPhase] = useState<Phase>("idle");
  const startedAt = useRef(0);

  useEffect(() => {
    runInGlobalTransition = startTransition;
    return () => {
      runInGlobalTransition = null;
    };
  }, [startTransition]);

  useEffect(() => {
    if (isPending) {
      // Re-entering while already loading (a click during a click) keeps the
      // original start time, so the bar does not restart its fill.
      if (phase !== "loading") {
        startedAt.current = Date.now();
        setPhase("loading");
      }
      return;
    }
    if (phase !== "loading") return;

    // Work is done. Hold the bar until it has been readable, then run it to
    // full width and fade it out.
    const hold = remainingHoldMs(startedAt.current, Date.now());
    const toDone = window.setTimeout(() => setPhase("done"), hold);
    const toIdle = window.setTimeout(
      () => setPhase("idle"),
      hold + PROGRESS_FADE_MS
    );
    return () => {
      window.clearTimeout(toDone);
      window.clearTimeout(toIdle);
    };
  }, [isPending, phase]);

  const visible = phase !== "idle";

  return (
    <div
      role="status"
      aria-live="polite"
      aria-busy={isPending}
      className="pointer-events-none fixed inset-x-0 top-0 z-[100] h-0.5"
    >
      <span className="sr-only">{isPending ? "Working…" : ""}</span>
      <div
        data-phase={phase}
        className={cn(
          "h-full bg-primary",
          // The fill runs while loading; `done` overrides it with a width
          // transition to 100%, which needs the animation switched off or the
          // two fight over the same property.
          "motion-safe:data-[phase=loading]:animate-global-progress",
          // Reduced motion still gets a signal, just a static one: the bar is
          // simply present at full width and fades, with no travel.
          "motion-reduce:data-[phase=loading]:w-full",
          phase === "done" &&
            "w-full opacity-0 transition-[width,opacity] duration-300 ease-out",
          phase === "idle" && "w-0 opacity-0",
          phase === "loading" && "opacity-100"
        )}
        style={
          // `done` must not keep the keyframes' final width, or the run-out to
          // 100% never happens.
          phase === "done" ? { animation: "none" } : undefined
        }
      >
        {/* The bar itself is the indicator; nothing renders inside it. */}
        <span className="sr-only">{visible ? "Loading" : ""}</span>
      </div>
    </div>
  );
}
