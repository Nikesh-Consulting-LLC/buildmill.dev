"use client";

import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import type { HelpSection } from "./help-content";

/** US-2.30: sticky in-page table of contents (desktop right rail; hidden
 * on mobile). Highlights the section currently in view.
 *
 * US-74.6: the sections are passed in — each topic page has its own few,
 * where the one-page handbook had a single global list. */
export function HelpToc({ sections }: { sections: readonly HelpSection[] }) {
  const [active, setActive] = useState<string>(sections[0]?.id ?? "");

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible[0]) setActive(visible[0].target.id);
      },
      { rootMargin: "-10% 0px -70% 0px" }
    );
    for (const { id } of sections) {
      const el = document.getElementById(id);
      if (el) observer.observe(el);
    }
    return () => observer.disconnect();
  }, [sections]);

  // One section is its own table of contents — the rail would just repeat the
  // heading already at the top of the page.
  if (sections.length < 2) return null;

  return (
    <nav aria-label="On this page" className="hidden w-40 shrink-0 lg:block">
      <div className="sticky top-8 flex flex-col gap-0.5 border-l pl-3">
        {sections.map((section) => (
          <a
            key={section.id}
            href={`#${section.id}`}
            className={cn(
              "rounded px-2 py-1 text-sm transition-colors",
              active === section.id
                ? "font-medium text-foreground"
                : "text-muted-foreground hover:text-foreground"
            )}
          >
            {section.label}
          </a>
        ))}
      </div>
    </nav>
  );
}
