import { redirect } from "next/navigation";

/** us-2.20: the standalone review queue is retired — Things to Do lists
 * every pending review with more context. Detail pages (/review/[issueId])
 * remain the place where approving happens. */
export default function ReviewPage() {
  redirect("/dashboard");
}
