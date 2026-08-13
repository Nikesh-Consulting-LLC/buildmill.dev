// apps/web/src/lib/active-org.ts
//
// US-9.7: a human in more than one org picks which one they're working in. The
// active org is persisted on principals.active_org_id (server-readable) and
// every org-scoped page resolves through this helper — falling back to the
// first membership when unset or when the stored org is no longer a valid,
// active membership, so leaving/suspension/deletion self-heals.

import type { SupabaseClient } from "@supabase/supabase-js";

export type OrgOption = {
  orgId: string;
  name: string;
  shortname: string | null;
  // SuperAdmin nav/routes are scoped to whichever workspace is active, not
  // to "is a member of the platform-admin org somewhere" — switching away
  // from it should hide/block admin surfaces, not leave them dangling.
  isPlatformAdmin: boolean;
};

export type ActiveOrg = { orgId: string | null; orgs: OrgOption[] };

/** US-87.1: the caller's principal row, read once per request and shared by
 * the password gate, the shell's principal id, and org resolution. */
export type CallerPrincipal = {
  id: string;
  must_change_password: boolean | null;
  active_org_id: string | null;
};

export async function resolveActiveOrg(
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  supabase: SupabaseClient<any, any, any>,
  userId: string,
): Promise<ActiveOrg> {
  const { data: principal } = await supabase
    .from("principals")
    .select("active_org_id")
    .eq("auth_user_id", userId)
    .maybeSingle();
  return resolveActiveOrgFrom(supabase, userId, principal?.active_org_id ?? null);
}

/** US-87.1: the same resolution, for callers that have ALREADY read the
 * principal row. `resolveActiveOrg` used to read it itself, which made it the
 * third reader of one row in a single shell render. Splitting the read from
 * the decision lets `request-cache.ts` supply the stored org it already has,
 * without changing the fallback rules below. */
export async function resolveActiveOrgFrom(
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  supabase: SupabaseClient<any, any, any>,
  userId: string,
  stored: string | null,
): Promise<ActiveOrg> {
  const { data: memberships } = await supabase
    .from("organization_members")
    .select("org_id, created_at, organizations(name, shortname, is_platform_admin)")
    .eq("user_id", userId)
    .eq("status", "active")
    .order("created_at", { ascending: true });

  const rows = (memberships ?? []) as unknown as Array<{
    org_id: string;
    organizations: {
      name: string | null;
      shortname: string | null;
      is_platform_admin: boolean | null;
    } | null;
  }>;
  const orgs: OrgOption[] = rows.map((m) => ({
    orgId: m.org_id,
    name: m.organizations?.name ?? "Organization",
    shortname: m.organizations?.shortname ?? null,
    isPlatformAdmin: m.organizations?.is_platform_admin ?? false,
  }));

  if (orgs.length === 0) return { orgId: null, orgs: [] };

  const orgId = stored && orgs.some((o) => o.orgId === stored) ? stored : orgs[0].orgId;
  return { orgId, orgs };
}

/** Whether the CURRENTLY ACTIVE workspace is the platform-admin org — the
 * gate for SuperAdmin nav/routes. Deliberately narrower than "is a member
 * of the platform-admin org" (is_platform_admin() RPC): a superadmin who
 * switches to a regular workspace should see and reach the same surfaces
 * a regular member would, not a stale admin menu that no longer applies. */
export function isActiveOrgPlatformAdmin(active: ActiveOrg): boolean {
  return !!active.orgs.find((o) => o.orgId === active.orgId)?.isPlatformAdmin;
}
