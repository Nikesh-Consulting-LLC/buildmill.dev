import { ViewTransition } from "react";
import { redirect } from "next/navigation";
import { isActiveOrgPlatformAdmin } from "@/lib/active-org";
import {
  getServerClient,
  getCurrentUser,
  getCallerPrincipal,
  getProfile,
  getActiveOrg,
} from "@/lib/request-cache";
import { AppSidebar } from "@/components/app-sidebar";
import { MobileNav } from "@/components/mobile-nav";
import { ShellLiveCount } from "@/components/shell-live-count";
import { WebVitalsReporter } from "@/components/web-vitals-reporter";
import { GlobalProgressBar } from "@/components/global-progress-bar";
import { Toaster } from "@/components/ui/toast";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { getPendingCount } from "./workbench/data";

export default async function AppLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const supabase = await getServerClient();

  const user = await getCurrentUser();
  if (!user) {
    redirect("/login");
  }

  // US-87.1: the principal row, the profile and the org resolution do not
  // depend on each other, so they resolve together rather than in three
  // sequential round trips. Each is `React.cache()`d, so a page rendering
  // under this shell that needs the same fact gets it for free — and
  // `principals` is read ONCE for what used to be three reads of one row.
  const [principal, profile, activeOrg] = await Promise.all([
    getCallerPrincipal(user.id),
    getProfile(user.id),
    getActiveOrg(user.id),
  ]);

  // US-9.5: forced first-login password change. Re-checked server-side on every
  // (app) route, so deep-linking while flagged can't bypass it. /change-password
  // lives in the (auth) group, outside this layout, so the redirect never loops.
  // Caching must never let a flagged principal through: `React.cache` lives
  // for exactly one render pass, so the next request re-reads (us-87.1 AC4).
  if (principal?.must_change_password) {
    redirect("/change-password");
  }

  // us-94.1: the beta gate. A new signup authenticates but waits for a
  // platform admin's approval; until then every (app) route — deep links
  // included — lands on /gate, which lives in the (auth) group so this
  // redirect can never loop. Missing profile rows gate too: the safe
  // default is the closed door. Re-read per request (React.cache spans one
  // render pass), so approval takes effect on the next page load (AC4).
  if (!profile?.approved_at) {
    redirect("/gate");
  }

  // US-9.12: the caller's principal + recent notifications for the shell bell.
  const principalId = principal?.id ?? "";

  const { orgId: resolvedOrgId, orgs } = activeOrg;
  const orgId = resolvedOrgId ?? "";
  // SuperAdmin nav is scoped to the active workspace, not "is a member of
  // the platform-admin org somewhere" — switching workspaces updates it.
  const isSuperadmin = isActiveOrgPlatformAdmin(activeOrg);

  // US-6.1: the pending-decision count powers the sidebar badge, the tab
  // title, and the realtime subscription. US-87.2 makes it a count rather
  // than the whole Things-to-Do dataset, while keeping one definition shared
  // with the page, so the badge and the page header can never disagree.
  // Both of these need `orgId`, so they follow the batch above — but they do
  // not need each other, so they run together.
  const [pendingCount, notificationsResult] = await Promise.all([
    orgId ? getPendingCount(orgId) : Promise.resolve(0),
    principalId && orgId
      ? supabase
          .from("notifications")
          .select("id, type, payload, read_at, created_at")
          .eq("org_id", orgId)
          .order("created_at", { ascending: false })
          .limit(30)
      : Promise.resolve({ data: [] }),
  ]);
  const notifications = notificationsResult.data;

  return (
    <div className="flex min-h-0 w-full flex-1 flex-col md:flex-row">
      <GlobalProgressBar />
      <WebVitalsReporter orgId={orgId || null} userId={user.id} />
      <ShellLiveCount count={pendingCount} orgId={orgId} />
      <MobileNav
        isSuperadmin={isSuperadmin}
        email={user.email ?? ""}
        displayName={profile?.display_name ?? null}
        avatarUrl={profile?.avatar_url ?? null}
        badgeCount={pendingCount}
        orgs={orgs}
        activeOrgId={orgId || null}
      />
      <AppSidebar
        isSuperadmin={isSuperadmin}
        email={user.email ?? ""}
        displayName={profile?.display_name ?? null}
        avatarUrl={profile?.avatar_url ?? null}
        badgeCount={pendingCount}
        orgs={orgs}
        activeOrgId={orgId || null}
        principalId={principalId}
        notifications={notifications ?? []}
      />
      {/* US-87.11: one transition boundary around the page content, so a
          route change crossfades and a `loading.tsx` skeleton hands off to
          real content instead of being replaced under the cursor. It wraps
          only <main> — the sidebar and header must stay put, or the whole
          viewport appears to reload on every navigation. */}
      <main className="min-h-0 min-w-0 flex-1 overflow-y-auto p-4 md:p-6">
        <ViewTransition enter="content-in" exit="content-out">
          {children}
        </ViewTransition>
      </main>
      <Toaster />
      <ConfirmDialog />
    </div>
  );
}
