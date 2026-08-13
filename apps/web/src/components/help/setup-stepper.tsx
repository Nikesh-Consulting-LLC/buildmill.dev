"use client";

import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { SETUP_GUIDES, type SetupGuideKey } from "./help-content";
import type { HelpText } from "./use-help-text";

/** US-2.30: numbered visual steppers — one sentence per step, each linking to
 * the page where it happens.
 *
 * US-74.6: `only` picks the guides for one topic, in the order given, so each
 * guide sits with the thing it sets up instead of in one undifferentiated
 * wall of six. Omitted, every guide renders. */
export function SetupGuides({
  text,
  only,
}: {
  text: HelpText;
  only?: readonly SetupGuideKey[];
}) {
  const guides = only
    ? only
        .map((k) => SETUP_GUIDES.find((g) => g.key === k))
        .filter((g): g is (typeof SETUP_GUIDES)[number] => !!g)
    : SETUP_GUIDES;
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      {guides.map((guide) => (
        <Card key={guide.key}>
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <guide.icon className="size-4 text-muted-foreground" />
              {guide.title}
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            {guide.steps.map((step, i) => (
              <div key={step.textKey} className="flex gap-3">
                <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-primary/10 text-[11px] font-semibold text-primary">
                  {i + 1}
                </span>
                <span className="min-w-0 text-sm">
                  {text(step.textKey)}{" "}
                  <Link
                    href={step.href}
                    className="inline-flex items-center gap-0.5 whitespace-nowrap text-xs font-medium text-primary hover:underline"
                  >
                    {step.linkLabel}
                    <ArrowRight className="size-3" />
                  </Link>
                </span>
              </div>
            ))}
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
