"use client";

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { TemplateCard, type TemplateFace } from "@/components/template-card";
import { cn } from "@/lib/utils";
import {
  categoriesOf,
  filterTemplates,
  showCategoryChips,
  showFilterBox,
  type PickableTemplate,
} from "@/lib/template-picker";

/** US-118.3: the New project template row — one horizontal, scroll-snapping
 * row of cards, three in view and the edge of a fourth at the dialog's
 * width, so the dialog is the same height whether the org has three
 * templates or thirty. Native scroll (wheel, trackpad, touch, drag) with
 * prev/next buttons that hide at the ends, a fade on the side that has
 * more, page dots, and a radio group on the keyboard: arrows move the
 * selection and slide it into view, Space/Enter pick.
 *
 * Chips appear only at two or more categories and narrow the row; a filter
 * box only past six templates. Neither ever changes the selection — the line
 * under the row states it, so a selection hidden by a filter is still said.
 *
 * The one layout trap, named: this scroller lives inside grid-laid-out form
 * fields inside a grid dialog, and a flex scroller in that chain WIDENS the
 * dialog to its content instead of scrolling unless the chain can shrink —
 * hence `grid-cols-[minmax(0,1fr)]` on the wrapper and `min-w-0` on the
 * scroller. The mockup hit exactly this. */

export type RowTemplate = PickableTemplate & TemplateFace;

const CARD_WIDTH_CLASS = "w-[210px] max-sm:w-[78%]";

/** Smooth unless the person asked for less motion. */
function scrollBehavior(): ScrollBehavior {
  return typeof window !== "undefined" &&
    window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
    ? "auto"
    : "smooth";
}

export function TemplateRowPicker({
  templates,
  value,
  onChange,
  labelId,
  disabled = false,
}: {
  templates: RowTemplate[];
  value: string;
  onChange: (id: string) => void;
  /** id of the element that labels the group ("Start from a template"). */
  labelId: string;
  disabled?: boolean;
}) {
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const [category, setCategory] = useState<string>("all");
  const [query, setQuery] = useState("");
  const [nav, setNav] = useState({ canPrev: false, canNext: false, pages: 1, page: 0 });

  const chips = showCategoryChips(templates);
  const filterBox = showFilterBox(templates);
  const categories = useMemo(() => categoriesOf(templates), [templates]);
  const shown = useMemo(
    () => filterTemplates(templates, category, query),
    [templates, category, query],
  );
  const selected = templates.find((t) => t.id === value) ?? null;

  const updateNav = useCallback(() => {
    const el = scrollerRef.current;
    if (!el) return;
    const max = el.scrollWidth - el.clientWidth;
    const scrollable = max > 6;
    const canPrev = scrollable && el.scrollLeft > 6;
    const canNext = scrollable && el.scrollLeft < max - 6;
    const pages = scrollable ? Math.ceil(el.scrollWidth / el.clientWidth) : 1;
    let page = scrollable ? Math.min(pages - 1, Math.round(el.scrollLeft / el.clientWidth)) : 0;
    if (scrollable && el.scrollLeft >= max - 6) page = pages - 1;
    setNav((prev) =>
      prev.canPrev === canPrev && prev.canNext === canNext && prev.pages === pages && prev.page === page
        ? prev
        : { canPrev, canNext, pages, page },
    );
  }, []);

  // Recompute after every layout the row could have changed: mount, a
  // filter, a resize, or the dialog's open animation settling.
  useLayoutEffect(() => {
    updateNav();
  }, [updateNav, shown.length]);
  useEffect(() => {
    const el = scrollerRef.current;
    if (!el) return;
    let raf = 0;
    const onScroll = () => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        updateNav();
      });
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    const ro = new ResizeObserver(() => updateNav());
    ro.observe(el);
    return () => {
      el.removeEventListener("scroll", onScroll);
      ro.disconnect();
      if (raf) cancelAnimationFrame(raf);
    };
  }, [updateNav]);

  // A filter narrows the row; start it from the left again.
  useEffect(() => {
    const el = scrollerRef.current;
    if (el) el.scrollTo({ left: 0 });
  }, [category, query]);

  const ensureVisible = useCallback((id: string) => {
    const el = scrollerRef.current;
    if (!el) return;
    const card = el.querySelector<HTMLElement>(`[data-template-id="${id}"]`);
    if (!card) return;
    const cr = card.getBoundingClientRect();
    const sr = el.getBoundingClientRect();
    if (cr.left < sr.left + 3) el.scrollBy({ left: cr.left - sr.left - 3, behavior: scrollBehavior() });
    else if (cr.right > sr.right - 3) el.scrollBy({ left: cr.right - sr.right + 3, behavior: scrollBehavior() });
  }, []);

  function pick(id: string, focus = false) {
    onChange(id);
    ensureVisible(id);
    if (focus) {
      scrollerRef.current
        ?.querySelector<HTMLElement>(`[data-template-id="${id}"]`)
        ?.focus({ preventScroll: true });
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    if (disabled || shown.length === 0) return;
    const ids = shown.map((t) => t.id);
    const focused = (e.target as HTMLElement).closest<HTMLElement>("[data-template-id]");
    const i = focused ? ids.indexOf(focused.dataset.templateId ?? "") : -1;
    if (i < 0) return;
    let next: string | null = null;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") next = ids[(i + 1) % ids.length];
    if (e.key === "ArrowLeft" || e.key === "ArrowUp") next = ids[(i - 1 + ids.length) % ids.length];
    if (e.key === "Home") next = ids[0];
    if (e.key === "End") next = ids[ids.length - 1];
    if (e.key === " " || e.key === "Enter") {
      e.preventDefault();
      pick(ids[i]);
      return;
    }
    if (next) {
      e.preventDefault();
      pick(next, true);
    }
  }

  function step(direction: 1 | -1) {
    const el = scrollerRef.current;
    if (!el) return;
    el.scrollBy({ left: direction * (el.clientWidth - 40), behavior: scrollBehavior() });
  }

  // Roving tabindex: the selected card if it is shown, else the first shown.
  const tabStop = shown.some((t) => t.id === value) ? value : (shown[0]?.id ?? "");

  return (
    <div className="grid gap-2">
      {(chips || filterBox) && (
        <div className="flex flex-wrap items-center gap-2">
          {chips && (
            <div className="flex flex-wrap gap-1.5" aria-label="Filter by category">
              {["all", ...categories].map((c) => {
                const on = category === c;
                return (
                  <button
                    key={c}
                    type="button"
                    aria-pressed={on}
                    onClick={() => setCategory(c)}
                    className={cn(
                      "h-6 rounded-full border px-2.5 text-xs outline-none transition-colors focus-visible:ring-[3px] focus-visible:ring-ring/50",
                      on
                        ? "border-primary bg-primary text-primary-foreground"
                        : "border-border text-muted-foreground hover:bg-muted hover:text-foreground",
                    )}
                  >
                    {c === "all" ? "All" : c}
                  </button>
                );
              })}
            </div>
          )}
          {filterBox && (
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Filter templates…"
              aria-label="Filter templates"
              className="ml-auto h-7 max-w-[200px] text-xs"
            />
          )}
        </div>
      )}

      <div
        className={cn(
          "relative grid grid-cols-[minmax(0,1fr)]",
          "before:pointer-events-none before:absolute before:inset-y-[3px] before:left-0 before:z-[1] before:w-11 before:bg-gradient-to-r before:from-popover before:to-transparent before:opacity-0 before:transition-opacity",
          "after:pointer-events-none after:absolute after:inset-y-[3px] after:right-0 after:z-[1] after:w-11 after:bg-gradient-to-l after:from-popover after:to-transparent after:opacity-0 after:transition-opacity",
          nav.canPrev && "before:opacity-100",
          nav.canNext && "after:opacity-100",
        )}
      >
        {nav.canPrev && (
          <Button
            type="button"
            variant="outline"
            size="icon-sm"
            aria-label="Previous templates"
            onClick={() => step(-1)}
            className="absolute -left-3 top-1/2 z-[2] size-7 -translate-y-1/2 rounded-full shadow"
          >
            <ChevronLeft className="size-4" />
          </Button>
        )}
        <div
          ref={scrollerRef}
          role="radiogroup"
          aria-labelledby={labelId}
          onKeyDown={onKeyDown}
          className={cn(
            "-m-[3px] flex min-w-0 snap-x snap-mandatory gap-2.5 overflow-x-auto p-[3px]",
            "[scrollbar-width:none] [&::-webkit-scrollbar]:hidden",
          )}
        >
          {shown.length === 0 && (
            <p className="py-6 text-sm text-muted-foreground">No templates match.</p>
          )}
          {shown.map((t) => {
            const on = t.id === value;
            return (
              <TemplateCard
                key={t.id}
                template={t}
                selected={on}
                disabled={disabled}
                role="radio"
                aria-checked={on}
                tabIndex={t.id === tabStop ? 0 : -1}
                data-template-id={t.id}
                onClick={() => pick(t.id, true)}
                className={cn("shrink-0 snap-start", CARD_WIDTH_CLASS)}
              />
            );
          })}
        </div>
        {nav.canNext && (
          <Button
            type="button"
            variant="outline"
            size="icon-sm"
            aria-label="Next templates"
            onClick={() => step(1)}
            className="absolute -right-3 top-1/2 z-[2] size-7 -translate-y-1/2 rounded-full shadow"
          >
            <ChevronRight className="size-4" />
          </Button>
        )}
      </div>

      {nav.pages > 1 && (
        <div className="flex h-2 items-center justify-center gap-1.5" aria-hidden="true">
          {Array.from({ length: nav.pages }, (_, i) => (
            <span
              key={i}
              className={cn(
                "size-1.5 rounded-full",
                i === nav.page ? "bg-muted-foreground" : "bg-border",
              )}
            />
          ))}
        </div>
      )}

      <p className="text-xs text-muted-foreground">
        {selected ? (
          <>
            Selected: <span className="font-medium text-foreground">{selected.name}</span>
            {" — seeds this project's Agent Instructions and worker instructions from the template; editable afterward."}
          </>
        ) : (
          <>Seeds this project&apos;s Agent Instructions and worker instructions from the template — editable afterward.</>
        )}
      </p>
    </div>
  );
}
