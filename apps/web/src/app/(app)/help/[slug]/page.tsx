import { notFound } from "next/navigation";
import { HELP_TOPICS } from "@/components/help/help-content";
import { HelpTopicView } from "@/components/help/help-topic-view";

/** US-74.6: one page per handbook topic. Server component so `params` can be
 * awaited (Next 16); the body is client because every string resolves through
 * the useHelpText hook. */

export function generateStaticParams() {
  return HELP_TOPICS.map((t) => ({ slug: t.slug }));
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const topic = HELP_TOPICS.find((t) => t.slug === slug);
  return { title: topic ? `${topic.title} · Help` : "Help" };
}

export default async function HelpTopicPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  if (!HELP_TOPICS.some((t) => t.slug === slug)) notFound();
  return <HelpTopicView slug={slug} />;
}
