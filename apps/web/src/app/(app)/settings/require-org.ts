// apps/web/src/app/(app)/settings/require-org.ts
//
// US-2.24: every settings subpage needs the same auth + org preamble.

import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";
import { resolveActiveOrg } from "@/lib/active-org";

export async function requireOrg() {
  const supabase = await createClient();

  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  // US-9.7: honor the persisted active-org selection (falls back to the first
  // membership; null only when the user belongs to no org).
  const { orgId } = await resolveActiveOrg(supabase, user.id);
  if (!orgId) redirect("/login");

  return { supabase, user, orgId };
}
