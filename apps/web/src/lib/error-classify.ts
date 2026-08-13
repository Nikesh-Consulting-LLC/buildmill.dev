/**
 * US-79.4 (prod BUG-5): recognize the errors that mean "the network, not the
 * app". WebKit reports any failed fetch as `TypeError: Load failed` — the
 * user's connection dropping and a hashed JS chunk that a deploy replaced
 * mid-session read identically, and neither names a URL. Classifying them lets
 * the report say what it is, and lets the boundary try the standard cure
 * (one reload) before showing the jam screen.
 *
 * Zero imports on purpose: this is pure signature matching, and the test runs
 * it without dragging the Supabase client along.
 */

const NETWORK_SIGNATURES = [
  "load failed", // WebKit: any failed fetch, including dynamic imports
  "failed to fetch", // Chromium
  "networkerror when attempting to fetch resource", // Firefox
  "error loading dynamically imported module", // stale chunk (Firefox/WebKit)
  "importing a module script failed", // stale chunk (WebKit)
  "chunkloaderror", // stale chunk (webpack)
  "could not reach the api", // our own NetworkError (lib/api.ts)
];

/** "network" when the error is a connectivity/stale-chunk failure; null when
 * it is (as far as the signature can tell) the app's own defect. */
export function classifyError(error: {
  name?: string;
  message?: string;
}): "network" | null {
  const text = `${error.name ?? ""}: ${error.message ?? ""}`.toLowerCase();
  return NETWORK_SIGNATURES.some((s) => text.includes(s)) ? "network" : null;
}
