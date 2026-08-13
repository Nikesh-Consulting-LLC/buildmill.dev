import { redirect } from "next/navigation";

// US-57.7: presets are platform-authored now (US-57.6) — creating, editing,
// deleting and re-seeding all moved to the superadmin. This surface retires;
// nothing org-facing replaces it, since there is no longer an org-side
// decision to make here.
export default function PresetsRedirect() {
  redirect("/settings");
}
