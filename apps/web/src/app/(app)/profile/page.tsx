import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";
import { resolveActiveOrg } from "@/lib/active-org";
import { loadOrgCapabilities } from "@/lib/permissions";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ProfileForm } from "./profile-form";
import { ChangePasswordForm } from "./change-password-form";
import { MyTokens, type TokenRow } from "../settings/tokens/my-tokens";

export default async function ProfilePage() {
  const supabase = await createClient();

  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const { data: profile } = await supabase
    .from("profiles")
    .select("id, email, display_name, avatar_url")
    .eq("id", user.id)
    .maybeSingle();

  // US-9.17: personal access tokens + org info, scoped to the active org (US-9.7).
  const { orgId } = await resolveActiveOrg(supabase, user.id);
  let tokens: TokenRow[] = [];
  let canDevelop = false;
  let org: { name: string; shortname: string | null } | null = null;
  if (orgId) {
    const { data: orgRow } = await supabase
      .from("organizations")
      .select("name, shortname")
      .eq("id", orgId)
      .maybeSingle();
    org = orgRow ?? null;
    const { can } = await loadOrgCapabilities(supabase, orgId, user.id);
    canDevelop = can("develop");
    const { data: principal } = await supabase
      .from("principals")
      .select("id")
      .eq("auth_user_id", user.id)
      .maybeSingle();
    if (principal) {
      const { data } = await supabase
        .from("workers")
        .select("id, name, token_last4, status, last_seen_at, created_at")
        .eq("org_id", orgId)
        .eq("principal_id", principal.id)
        .eq("type", "human")
        .order("created_at", { ascending: true });
      tokens = (data ?? []) as TokenRow[];
    }
  }

  return (
    <div className="flex w-full flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Profile</h1>
        <p className="text-sm text-muted-foreground">
          Your name, avatar, password, organization, and access tokens.
        </p>
      </div>
      <div className="grid items-start gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Your profile</CardTitle>
            <CardDescription>
              Visible to the rest of your organization.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <ProfileForm
              userId={user.id}
              email={profile?.email ?? user.email ?? ""}
              displayName={profile?.display_name ?? null}
              avatarUrl={profile?.avatar_url ?? null}
            />
            {org && (
              <div className="grid gap-2 border-t pt-4">
                <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  Organization
                </p>
                <div className="grid gap-3 sm:grid-cols-2">
                  <div>
                    <p className="text-xs text-muted-foreground">Name</p>
                    <p className="text-sm font-medium">{org.name}</p>
                  </div>
                  <div>
                    <p className="text-xs text-muted-foreground">Slug</p>
                    <p className="font-mono text-sm">{org.shortname ?? "—"}</p>
                  </div>
                </div>
              </div>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Change password</CardTitle>
            <CardDescription>
              Requires your current password.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ChangePasswordForm email={profile?.email ?? user.email ?? ""} />
          </CardContent>
        </Card>
        {orgId && (
          <Card className="lg:col-span-2">
            <CardHeader>
              <CardTitle>Access tokens</CardTitle>
              <CardDescription>
                Personal router tokens — clone, commit, and push are attributed
                to you, and a token doubles as your git / MCP password. Create as
                many as you need (one per tool), reveal them again with Show, or
                disable one you no longer use. Shown in full once on creation.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <MyTokens orgId={orgId} canDevelop={canDevelop} tokens={tokens} />
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
