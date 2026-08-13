import { redirect } from "next/navigation";

/** US-23.2: the release detail page moved to /releases/[releaseId]. */
export default async function ProjectReleaseRedirect({
  params,
}: {
  params: Promise<{ releaseId: string }>;
}) {
  const { releaseId } = await params;
  redirect(`/releases/${releaseId}`);
}
