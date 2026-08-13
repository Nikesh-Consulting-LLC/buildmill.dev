import { redirect } from "next/navigation";

/** US-23.2: releases moved to the top-level hub. Deep links to a project's
 * Releases page exist in commit messages, notifications and the dashboard, so
 * this redirects rather than 404s. */
export default async function ProjectReleasesRedirect() {
  redirect("/releases");
}
