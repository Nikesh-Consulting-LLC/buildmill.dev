// apps/web/src/lib/permissions.ts
//
// US-9.2: capability layer. Authorization is expressed as capabilities, not
// hard-coded role names — the role->capability grid lives in `role_capabilities`
// (a global default the Superadmin edits, US-9.3) and every UI gate resolves
// through it, mirroring the `has_org_capability()` RLS helper so the client and
// the database agree. Roles apply to both humans and agents (US-9.1).

import type { SupabaseClient } from "@supabase/supabase-js";

export const ROLES = [
  "owner",
  "admin",
  "lead",
  "developer",
  "reviewer",
  "viewer",
] as const;
export type Role = (typeof ROLES)[number];

export const CAPABILITIES = [
  "manage_org",
  "manage_members",
  "manage_project",
  "manage_work",
  "review_work",
  "develop",
  "view",
] as const;
export type Capability = (typeof CAPABILITIES)[number];

export type OrgCapabilities = {
  role: Role | null;
  can: (capability: Capability) => boolean;
};

const DENY: OrgCapabilities = { role: null, can: () => false };

// Resolve the caller's role in an org and the capabilities that role grants.
// Reads the same `role_capabilities` rows the RLS helper does; the database
// stays authoritative (RLS re-checks every write), this only drives the UI.
export async function loadOrgCapabilities(
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  supabase: SupabaseClient<any, any, any>,
  orgId: string,
  userId: string,
): Promise<OrgCapabilities> {
  const { data: membership } = await supabase
    .from("organization_members")
    .select("role")
    .eq("org_id", orgId)
    .eq("user_id", userId)
    .maybeSingle();

  const role = (membership?.role ?? null) as Role | null;
  if (!role) return DENY;

  const { data: caps } = await supabase
    .from("role_capabilities")
    .select("capability")
    .eq("role", role)
    .eq("allowed", true);

  const granted = new Set<string>((caps ?? []).map((c) => c.capability as string));
  return { role, can: (capability) => granted.has(capability) };
}

export const ROLE_LABELS: Record<Role, string> = {
  owner: "Owner",
  admin: "Admin",
  lead: "Lead",
  developer: "Developer",
  reviewer: "Reviewer",
  viewer: "Viewer",
};

export const CAPABILITY_LABELS: Record<Capability, string> = {
  manage_org: "Manage org",
  manage_members: "Manage members",
  manage_project: "Manage projects",
  manage_work: "Manage work",
  review_work: "Review work",
  develop: "Develop",
  view: "View",
};

export const CAPABILITY_DESCRIPTIONS: Record<Capability, string> = {
  manage_org: "Rename, archive, or delete the organization",
  manage_members: "Provision principals, assign roles, remove members",
  manage_project: "Repos, build config, secrets, deployments, guidelines-ready",
  manage_work: "Create, dispatch, and assign work items & runs",
  review_work: "Review, approve, merge, and send back",
  develop: "Claim runs, own router tokens, push via the router",
  view: "Read-only access to org data",
};

// Cells the Superadmin cannot toggle off (re-enforced server-side, US-9.3):
// an org must always have a role that can manage it, and read access is the floor.
export function isLockedCapability(role: Role, capability: Capability): boolean {
  if (capability === "view") return true;
  if (role === "owner" && capability === "manage_org") return true;
  return false;
}
