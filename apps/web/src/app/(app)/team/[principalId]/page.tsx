import { redirect } from "next/navigation";

/**
 * The standalone Overview page was a second UI for exactly what the Team
 * list already renders inline (a row's expand panel now carries this same
 * content — tokens, current run, project access, performance, history). This
 * route survives only so existing links (`/team/{id}`, the add-agent wizard,
 * dashboard/queue deep links) keep working — it forwards to the row instead
 * of rendering a competing page.
 */
export default async function MemberPage({
  params,
}: {
  params: Promise<{ principalId: string }>;
}) {
  const { principalId } = await params;
  redirect(`/team?expand=${principalId}`);
}
