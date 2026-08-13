import { redirect } from "next/navigation";

// US-9.15: the Workers ops view folded into the Team page's Live tab.
export default function WorkersRedirect() {
  redirect("/team?tab=live");
}
