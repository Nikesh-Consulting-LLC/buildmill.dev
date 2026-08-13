import { requireOrg } from "../require-org";
import SpendView from "./spend-view";

// US-9.7: resolved server-side and passed down with `key={orgId}` so the
// client view remounts (fresh state, not a stale cached org) whenever the
// workspace switcher changes the active org — router.refresh() alone
// doesn't reset a client component's own useState.
export default async function SpendPage() {
  const { orgId } = await requireOrg();
  return <SpendView orgId={orgId} key={orgId} />;
}
