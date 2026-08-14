// apps/web/src/lib/request-cache.ts
//
// US-87.1: request-scoped memoization for the facts every server render needs.
//
// The app shell wraps every page, and it used to read `principals` three times
// by the same key across six sequential round trips — once for the
// must-change-password gate, once for the shell's principal id, and once more
// inside `resolveActiveOrg`. Nothing deduped, because each call site built its
// own Supabase client and asked again.
//
// `React.cache()` is the right tool and the only safe one: its lifetime is a
// single server render pass, so two call sites in one request share an answer
// and two different requests never do. A module-level Map would leak one
// caller's principal — and their password gate — into another's request; that
// is the defect us-87.1's AC4 forbids, and it is why nothing here is a Map.
//
// Every helper takes only serializable arguments (or none) so the cache key is
// stable — passing a Supabase client as an argument would miss on every call,
// since each client is a fresh object.

import { cache } from "react";
import type { User } from "@supabase/supabase-js";
import { createClient } from "@/lib/supabase/server";
import {
  resolveActiveOrgFrom,
  type ActiveOrg,
  type CallerPrincipal,
} from "@/lib/active-org";
import { loadOrgCapabilities, type OrgCapabilities } from "@/lib/permissions";

/** One Supabase server client per request, so the helpers below share a
 * connection and their cache keys stay argument-free. */
export const getServerClient = cache(async () => createClient());

/** The authenticated caller. `auth.getUser()` is a network round trip to
 * Supabase Auth; the shell and the page underneath it need the same answer. */
export const getCurrentUser = cache(async (): Promise<User | null> => {
  const supabase = await getServerClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  return user ?? null;
});

/** The caller's principal row — one read serving all three historical
 * readers: the US-9.5 password gate, the shell's principal id (US-9.12), and
 * the active-org resolution (US-9.7). Fields are unioned deliberately: three
 * narrow selects of the same row cost three round trips and save nothing. */
export const getCallerPrincipal = cache(
  async (userId: string): Promise<CallerPrincipal | null> => {
    const supabase = await getServerClient();
    const { data } = await supabase
      .from("principals")
      .select("id, must_change_password, active_org_id")
      .eq("auth_user_id", userId)
      .maybeSingle();
    return (data as CallerPrincipal | null) ?? null;
  }
);

export type CallerProfile = {
  display_name: string | null;
  avatar_url: string | null;
  // us-94.1: null = waiting at the beta gate; the (app) layout redirects.
  approved_at: string | null;
};

export const getProfile = cache(
  async (userId: string): Promise<CallerProfile | null> => {
    const supabase = await getServerClient();
    const { data } = await supabase
      .from("profiles")
      .select("display_name, avatar_url, approved_at")
      .eq("id", userId)
      .maybeSingle();
    return (data as CallerProfile | null) ?? null;
  }
);

/** The active workspace, resolved from the already-read principal rather than
 * re-reading it. Callers that hold their own client keep using
 * `resolveActiveOrg` directly; this is the deduped path for the shell and the
 * pages it wraps. */
export const getActiveOrg = cache(
  async (userId: string): Promise<ActiveOrg> => {
    const supabase = await getServerClient();
    const principal = await getCallerPrincipal(userId);
    return resolveActiveOrgFrom(supabase, userId, principal?.active_org_id ?? null);
  }
);

/** us-95.1: the caller's capabilities in an org, shared between the shell's
 * nav gate and the /costs page's own server-side check — one answer per
 * request, resolved through the same US-9.2 grid RLS reads. */
export const getOrgCapabilities = cache(
  async (orgId: string, userId: string): Promise<OrgCapabilities> => {
    const supabase = await getServerClient();
    return loadOrgCapabilities(supabase, orgId, userId);
  }
);
