import { redirect } from "next/navigation";

// US-9.14: personal router tokens now live on your own row in the Team page.
export default function TokensSettingsRedirect() {
  redirect("/team");
}
