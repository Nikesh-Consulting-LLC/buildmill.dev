import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";
import { resolveActiveOrg, isActiveOrgPlatformAdmin } from "@/lib/active-org";

export default async function AdminLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  // Scoped to the ACTIVE workspace, not "is a member of the platform-admin
  // org somewhere" — matches the sidebar's SuperAdmin nav (app/layout.tsx),
  // so switching workspaces hides and blocks admin surfaces together rather
  // than leaving a stale menu item that 404s or a reachable page the nav
  // no longer shows.
  const activeOrg = await resolveActiveOrg(supabase, user.id);
  if (!isActiveOrgPlatformAdmin(activeOrg)) redirect("/dashboard");

  return <>{children}</>;
}
