import { redirect } from "next/navigation";

// us-95.1: the spend report moved to the top-level Costs section and the
// rates form to Settings → LLM Providers. This route survives only so old
// bookmarks land somewhere true — /costs runs the same server-side gate it
// would for any visitor.
export default function SpendPage() {
  redirect("/costs");
}
