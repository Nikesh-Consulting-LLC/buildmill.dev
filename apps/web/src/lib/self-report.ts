import { API_URL } from "./api";
import { classifyError } from "./error-classify";

/**
 * US-16.8: Build Mill reporting its own browser-side failures into the same
 * inbox every deployed app reports into.
 *
 * Configuration is two public env vars. Absent, this is a silent no-op — a
 * developer running locally should get nothing, not a failed POST on every
 * error. The key is public by design (it ships in the bundle, exactly like the
 * SDK's), and it can only write reports.
 */
const DEPLOYMENT = process.env.NEXT_PUBLIC_SELF_REPORT_DEPLOYMENT ?? "";
const KEY = process.env.NEXT_PUBLIC_SELF_REPORT_KEY ?? "";

// Reporting the failure of reporting is how self-instrumentation takes an app
// down. The guard is the difference between one dropped report and a loop.
let reporting = false;

export function reportSelfError(
  error: Error & { digest?: string },
  context: Record<string, unknown> = {},
): void {
  if (!DEPLOYMENT || !KEY || reporting) return;
  if (typeof window === "undefined") return;
  reporting = true;
  try {
    // US-79.4 (prod BUG-5): classify network/stale-chunk failures and carry
    // the failing request when the error knows it (NetworkError, lib/api.ts) —
    // "TypeError: Load failed" with no URL is a report nobody can act on.
    const kind = classifyError(error);
    const request = (error as { request?: unknown }).request;
    void fetch(`${API_URL}/api/v1/report/${DEPLOYMENT}/issues`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Report-Key": KEY },
      body: JSON.stringify({
        source: "automated",
        error_type: error.name || "Error",
        message: error.message || String(error),
        stack_trace: error.stack ?? null,
        context: {
          ...context,
          component: "apps/web",
          url: window.location.href,
          digest: error.digest ?? null,
          user_agent: navigator.userAgent,
          ...(kind ? { kind } : {}),
          ...(typeof request === "string" && request ? { request } : {}),
        },
      }),
      keepalive: true,
      credentials: "omit",
    }).catch(() => {
      /* best effort: a lost report is never a broken page */
    });
  } catch {
    /* swallowed, deliberately */
  } finally {
    reporting = false;
  }
}

/**
 * US-79.4: the standard cure for a stale-deploy chunk failure is one reload —
 * the new HTML references the new hashes. Guarded to once per browser session
 * so a genuinely broken page cannot reload-loop; the second failure falls
 * through to the jam screen. Returns true when a reload was initiated.
 *
 * Call AFTER reportSelfError: the report rides `keepalive: true`, so it
 * survives the navigation, and a recurrence after the reload dedupes
 * server-side into the same report's occurrence count.
 */
export function reloadOnceForNetworkError(error: Error): boolean {
  if (typeof window === "undefined") return false;
  if (classifyError(error) !== "network") return false;
  try {
    const KEY = "bm-network-reloaded";
    if (window.sessionStorage.getItem(KEY)) return false;
    window.sessionStorage.setItem(KEY, new Date().toISOString());
  } catch {
    // No sessionStorage means no loop guard — do not risk the loop.
    return false;
  }
  window.location.reload();
  return true;
}
