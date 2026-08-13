import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { requireOrg } from "../require-org";
import { GithubSettings } from "../github-settings";

export default async function GithubSettingsPage() {
  const { supabase, orgId } = await requireOrg();

  const { data: connections } = await supabase
    .from("github_connections")
    .select(
      "id, method, installation_id, account_login, account_type, pat_last4, pat_expires_at, repos"
    )
    .eq("org_id", orgId);

  // US-2.25: cross-reference available repos against linked projects.
  const { data: projects } = await supabase
    .from("projects")
    .select("id, name, repo_full_name, archived_at")
    .eq("org_id", orgId);

  return (
    <Card>
      <CardHeader>
        <CardTitle>GitHub</CardTitle>
        <CardDescription>
          Connect the Build Mill GitHub App, or paste a fine-grained
          personal access token, to link real repos and let the factory
          read/merge PRs on your org&apos;s behalf.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <GithubSettings
          connections={connections ?? []}
          projects={projects ?? []}
        />
      </CardContent>
    </Card>
  );
}
