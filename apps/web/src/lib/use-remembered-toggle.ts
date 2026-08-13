"use client";

import { useSyncExternalStore } from "react";

/** US-12.4: a checkbox whose last answer is remembered, so a manager who
 * always continues (or never does) is not asked the same question
 * identically every time — while the choice stays visible and revocable
 * before every commit.
 *
 * `useSyncExternalStore` rather than state-plus-effect: localStorage is an
 * external store, the server snapshot is the fallback (so SSR and the
 * first client render agree), and every mounted toggle on the same key
 * stays in sync. It also keeps the repo's `react-hooks/set-state-in-effect`
 * rule satisfied honestly rather than by suppression. */
const listeners = new Set<() => void>();

function subscribe(onChange: () => void) {
  listeners.add(onChange);
  return () => {
    listeners.delete(onChange);
  };
}

export function useRememberedToggle(
  key: string,
  fallback: boolean
): [boolean, (next: boolean) => void] {
  const value = useSyncExternalStore(
    subscribe,
    // Booleans are primitives, so a fresh read per call is a stable
    // snapshot — no caching needed to avoid a render loop.
    () => {
      try {
        const stored = window.localStorage.getItem(key);
        return stored === null ? fallback : stored === "1";
      } catch {
        return fallback;
      }
    },
    () => fallback
  );

  function update(next: boolean) {
    try {
      window.localStorage.setItem(key, next ? "1" : "0");
    } catch {
      // Blocked or full storage: the toggle simply won't be remembered.
    }
    listeners.forEach((l) => l());
  }

  return [value, update];
}
