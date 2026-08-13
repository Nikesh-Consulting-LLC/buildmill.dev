import { createClient } from "@/lib/supabase/server";
import { API_URL } from "@/lib/api";

/**
 * US-26.8: the bundle hash this API would install — the drift comparison
 * point. Server-side, so the fleet list can say "2 of 4 hosts are stale"
 * without every card asking separately.
 *
 * Returns null when the API is unreachable: a page that cannot reach the API
 * shows no drift claim at all, rather than telling you everything is current.
 */
export async function currentBundleHash(): Promise<string | null> {
  try {
    const supabase = await createClient();
    const {
      data: { session },
    } = await supabase.auth.getSession();
    if (!session) return null;
    const resp = await fetch(`${API_URL}/api/v1/agent-servers/current-version`, {
      headers: { Authorization: `Bearer ${session.access_token}` },
      cache: "no-store",
    });
    if (!resp.ok) return null;
    const body = (await resp.json()) as { bundle_hash?: string };
    return body.bundle_hash ?? null;
  } catch {
    return null;
  }
}
