import { redirect } from "next/navigation";
import {
  getCurrentUser,
  getActiveOrg,
  getOrgCapabilities,
} from "@/lib/request-cache";
import { Lock } from "lucide-react";
import { EmptyState } from "@/components/empty-state";
import { parseCostsParams } from "./costs-url";
import CostsView from "./costs-view";

// us-95.1: cost reporting's own room, and the door is checked here — server
// side, not in the nav. Hiding the sidebar entry is courtesy; this check is
// the gate, and it resolves through the same request-cached capability read
// the shell used to decide whether to show the entry at all.
export default async function CostsPage({
  searchParams,
}: {
  // us-95.4: the whole view lives in the URL, so a pasted link restores the
  // exact slice — group, window and filters parsed here, validated, and
  // handed to the client view as its initial state.
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const user = await getCurrentUser();
  if (!user) redirect("/login");

  const { orgId } = await getActiveOrg(user.id);
  if (!orgId) redirect("/login");

  const caps = await getOrgCapabilities(orgId, user.id);
  if (!caps.can("view_costs")) {
    // The turn-away is one plain sentence, not an error page and not a blank
    // (us-95.1 AC2). The underlying spend facts a member may already see
    // elsewhere (project cards, item costs) are unaffected.
    return (
      <EmptyState
        icon={Lock}
        title="Costs is visible to owners and admins"
        description="Your role in this workspace doesn't include cost reporting. Ask an owner or admin if you need it."
      />
    );
  }

  return (
    <CostsView
      key={orgId}
      orgId={orgId}
      initial={parseCostsParams(await searchParams)}
    />
  );
}
