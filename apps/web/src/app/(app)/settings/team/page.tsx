import { redirect } from "next/navigation";

// US-9.13: My Team folded into the top-level Team surface.
export default function TeamSettingsRedirect() {
  redirect("/team");
}
