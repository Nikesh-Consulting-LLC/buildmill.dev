"use client";

import { useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowRight } from "lucide-react";
import { MarkdownView } from "@/components/markdown-view";
import {
  HELP_TOPICS,
  LEGACY_SECTION_TOPIC,
  SECTION_TOPIC,
} from "@/components/help/help-content";
import { useHelpText } from "@/components/help/use-help-text";

/** US-2.30 / US-74.6: the handbook index. The operator's manual used to be one
 * long scroll; it is now a topic per page, and this is the way in. */
export default function HelpIndexPage() {
  const text = useHelpText();
  const router = useRouter();

  // US-74.6: `/help#pipeline` was a real link for as long as the handbook was
  // one page, and those bookmarks (and any link written into a story or a
  // comment) must keep landing on the content. The fragment never reaches the
  // server, so the forward happens here.
  useEffect(() => {
    const id = window.location.hash.slice(1);
    if (!id) return;
    const slug = SECTION_TOPIC[id] ?? LEGACY_SECTION_TOPIC[id];
    if (slug) router.replace(`/help/${slug}#${id}`);
  }, [router]);

  return (
    <div className="flex w-full flex-col gap-6 pb-12">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">
          How Build Mill works
        </h1>
        <MarkdownView className="mt-1 [&_p]:text-sm [&_p]:text-muted-foreground">
          {text("help/index/intro")}
        </MarkdownView>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        {HELP_TOPICS.map((topic) => (
          <Link
            key={topic.slug}
            href={`/help/${topic.slug}`}
            className="group flex min-w-0 flex-col gap-1.5 rounded-lg border p-4 transition-colors hover:border-ring/60 hover:bg-muted/40"
          >
            <span className="flex items-center gap-2">
              <topic.icon className="size-4 shrink-0 text-muted-foreground" />
              <span className="font-medium">{topic.title}</span>
              <ArrowRight className="size-3.5 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
            </span>
            <span className="text-sm text-muted-foreground">{topic.blurb}</span>
            <span className="mt-0.5 text-xs text-muted-foreground/80">
              {topic.sections.map((s) => s.label).join(" · ")}
            </span>
          </Link>
        ))}
      </div>
    </div>
  );
}
