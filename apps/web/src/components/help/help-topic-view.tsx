"use client";

import Link from "next/link";
import { ArrowLeft, ArrowRight } from "lucide-react";
import { MarkdownView } from "@/components/markdown-view";
import { ArchitectureMap } from "@/components/help/architecture-map";
import {
  HELP_POINTS,
  HELP_TOPICS,
  type HelpSection,
} from "@/components/help/help-content";
import { HelpToc } from "@/components/help/help-toc";
import { HeroFlow } from "@/components/help/hero-flow";
import { LifecycleFlowchart } from "@/components/help/lifecycle-flowchart";
import { PipelineWalkthrough } from "@/components/help/pipeline-walkthrough";
import { SetupGuides } from "@/components/help/setup-stepper";
import { StatusLegend } from "@/components/help/status-legend";
import { useHelpText, type HelpText } from "@/components/help/use-help-text";

/** US-74.6: one topic page of the handbook. The sections it holds come from
 * HELP_TOPICS; what each section renders is decided here, in one place, so
 * moving a section between topics is a data edit and nothing more.
 *
 * US-2.30 still holds: structure is code, every descriptive string resolves
 * through useHelpText (superadmin override, else the factory default). */

// US-35.4: the four words the app uses for its own objects, defined here and
// nowhere else. A glossary repeated per surface is a glossary that drifts.
const GLOSSARY = [
  {
    term: "Machine",
    def: "A box the factory reaches over SSH. It can be a deploy target, a host for coding agents, or both.",
  },
  {
    term: "Agent",
    def: "Something that does the work — a coding agent on one of your machines, or one you run yourself. It has a name, a role and a profile on Team, exactly like a person.",
  },
  {
    term: "Worker token",
    def: "The credential an agent connects with. It travels as the X-Worker-Token header, which is why it keeps that name.",
  },
  {
    term: "Preset",
    def: "A named bundle of run settings — model, effort, limits, tools — that an agent's routes pick from.",
  },
];

/** A run of headed paragraphs — the body of the topics that are prose. */
function HelpPoints({ id, text }: { id: string; text: HelpText }) {
  const points = HELP_POINTS[id] ?? [];
  return (
    <div className="flex flex-col gap-4">
      {points.map((p) => (
        <div key={p.textKey}>
          <h3 className="text-sm font-semibold">{p.heading}</h3>
          <MarkdownView className="mt-1 [&_p]:text-muted-foreground">
            {text(p.textKey)}
          </MarkdownView>
        </div>
      ))}
    </div>
  );
}

function SectionBody({
  section,
  text,
}: {
  section: HelpSection;
  text: HelpText;
}) {
  const bodies: Record<string, React.ReactNode> = {
    overview: (
      <div className="flex flex-col gap-3">
        <HeroFlow />
        <MarkdownView className="[&_p]:text-muted-foreground">
          {text("help/overview/intro")}
        </MarkdownView>
      </div>
    ),
    pipeline: <PipelineWalkthrough text={text} />,
    lifecycle: <LifecycleFlowchart />,
    statuses: <StatusLegend text={text} />,
    architecture: <ArchitectureMap text={text} />,
    glossary: (
      <dl className="grid gap-3 sm:grid-cols-2">
        {GLOSSARY.map((g) => (
          <div key={g.term} className="rounded-md border px-3 py-2">
            <dt className="text-sm font-medium">{g.term}</dt>
            <dd className="mt-0.5 text-sm text-muted-foreground">{g.def}</dd>
          </div>
        ))}
      </dl>
    ),
  };

  return (
    <div className="flex flex-col gap-5">
      {bodies[section.id] ?? <HelpPoints id={section.id} text={text} />}
      {section.guides && <SetupGuides text={text} only={section.guides} />}
    </div>
  );
}

/** The intro line above a section. Diagram sections have one authored
 * already; the prose topics carry their words in HELP_POINTS instead. */
const SECTION_INTRO: Record<string, string> = {
  pipeline: "help/pipeline/intro",
  lifecycle: "help/lifecycle/intro",
  architecture: "help/architecture/intro",
  statuses: "help/statuses/intro",
  setup: "help/setup/intro",
};

export function HelpTopicView({ slug }: { slug: string }) {
  const text = useHelpText();
  const index = HELP_TOPICS.findIndex((t) => t.slug === slug);
  const topic = HELP_TOPICS[index];
  if (!topic) return null;

  const prev = HELP_TOPICS[index - 1];
  const next = HELP_TOPICS[index + 1];

  return (
    <div className="flex w-full gap-10">
      {/* dashed flow lines drift forward; stilled for reduced motion */}
      <style>{`
        @media (prefers-reduced-motion: no-preference) {
          @keyframes help-dash { to { stroke-dashoffset: -20; } }
          .help-dash { animation: help-dash 1.4s linear infinite; }
        }
      `}</style>

      <div className="flex min-w-0 flex-1 flex-col gap-12 pb-12">
        <div className="flex flex-col gap-1">
          <Link
            href="/help"
            className="inline-flex w-fit items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="size-3" />
            Help
          </Link>
          <h1 className="text-2xl font-semibold tracking-tight">
            {topic.title}
          </h1>
          <p className="text-sm text-muted-foreground">{topic.blurb}</p>
        </div>

        {topic.sections.map((section) => {
          const introKey = SECTION_INTRO[section.id];
          return (
            <section key={section.id} id={section.id} className="scroll-mt-6">
              <h2 className="mb-1 text-lg font-semibold tracking-tight">
                {section.label}
              </h2>
              {introKey && (
                <MarkdownView className="mb-4 [&_p]:text-muted-foreground">
                  {text(introKey)}
                </MarkdownView>
              )}
              <SectionBody section={section} text={text} />
            </section>
          );
        })}

        <nav className="flex items-center justify-between gap-3 border-t pt-5">
          {prev ? (
            <Link
              href={`/help/${prev.slug}`}
              className="group flex min-w-0 flex-col text-left"
            >
              <span className="text-xs text-muted-foreground">Previous</span>
              <span className="flex items-center gap-1 text-sm font-medium group-hover:underline">
                <ArrowLeft className="size-3.5 shrink-0" />
                {prev.title}
              </span>
            </Link>
          ) : (
            <span />
          )}
          {next && (
            <Link
              href={`/help/${next.slug}`}
              className="group flex min-w-0 flex-col text-right"
            >
              <span className="text-xs text-muted-foreground">Next</span>
              <span className="flex items-center gap-1 text-sm font-medium group-hover:underline">
                {next.title}
                <ArrowRight className="size-3.5 shrink-0" />
              </span>
            </Link>
          )}
        </nav>
      </div>

      <HelpToc sections={topic.sections} />
    </div>
  );
}
