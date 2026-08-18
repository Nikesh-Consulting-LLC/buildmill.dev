"use client";

import { Check } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { MarkdownView } from "@/components/markdown-view";
import { cn } from "@/lib/utils";
import {
  templateCoverUrl,
  templateInitials,
  templateTint,
  type TemplateTint,
} from "@/lib/template-cover";

/** US-118.1: the face of a template, drawn the same way everywhere.
 *
 * `TemplateCover` is the picture — the stored image when there is one, else
 * a generated cover (initials on a tint) so the no-image state still looks
 * designed. `TemplateCard` composes it into the card a project creator picks
 * from (`variant="card"`) or the row Change template lists (`variant="row"`);
 * `TemplateThumb` is the 28px square beside a name in a list or header.
 * Radio semantics (`aria-checked`, roving tabindex) are the caller's — the
 * card only draws the selected ring and check. */

export type TemplateFace = {
  name: string;
  description?: string | null;
  image_path?: string | null;
  updated_at?: string | null;
  is_default?: boolean | null;
  /** A ready URL that wins over `image_path` — the details dialog's preview
   * of a picked-but-unsaved file. `null` forces the generated cover. */
  cover_url?: string | null;
};

const TINT_CLASSES: Record<TemplateTint, string> = {
  a: "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-200",
  b: "bg-orange-100 text-orange-800 dark:bg-orange-950 dark:text-orange-200",
  c: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200",
};

function supabaseUrl(): string | undefined {
  return process.env.NEXT_PUBLIC_SUPABASE_URL;
}

export function TemplateCover({
  template,
  className,
  initialsClassName,
}: {
  template: TemplateFace;
  className?: string;
  /** Size of the generated initials — the caller knows how big the cover is. */
  initialsClassName?: string;
}) {
  const url =
    template.cover_url !== undefined
      ? template.cover_url
      : templateCoverUrl(template.image_path, template.updated_at, supabaseUrl());
  if (url) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={url}
        alt=""
        aria-hidden="true"
        draggable={false}
        className={cn("block aspect-[2/1] w-full select-none object-cover", className)}
      />
    );
  }
  return (
    <span
      aria-hidden="true"
      className={cn(
        "relative grid aspect-[2/1] w-full select-none place-items-center overflow-hidden",
        TINT_CLASSES[templateTint(template.name)],
        className,
      )}
    >
      <span
        className={cn("font-semibold leading-none tracking-tight", initialsClassName ?? "text-4xl")}
      >
        {templateInitials(template.name)}
      </span>
      <span className="pointer-events-none absolute inset-0 bg-[repeating-linear-gradient(135deg,transparent_0_10px,rgba(0,0,0,0.03)_10px_11px)]" />
    </span>
  );
}

export function TemplateThumb({
  template,
  className,
}: {
  template: TemplateFace;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-block size-7 shrink-0 overflow-hidden rounded-sm border bg-muted",
        className,
      )}
    >
      <TemplateCover
        template={template}
        className="aspect-square h-full w-full"
        initialsClassName="text-[11px]"
      />
    </span>
  );
}

const ROW_CLASSES =
  "grid w-full grid-cols-[72px_minmax(0,1fr)_auto] items-center gap-3 rounded-md border bg-card p-2 pr-3 text-left text-sm";

function RowInner({
  template,
  keyBadge,
  meta,
}: {
  template: TemplateFace;
  keyBadge?: React.ReactNode;
  meta?: React.ReactNode;
}) {
  return (
    <>
      <span className="overflow-hidden rounded-sm border">
        <TemplateCover template={template} initialsClassName="text-lg" />
      </span>
      <span className="grid min-w-0 gap-0.5">
        <span className="flex min-w-0 flex-wrap items-center gap-1.5 font-medium leading-tight">
          <span className="truncate">{template.name}</span>
          {keyBadge}
          {template.is_default && <Badge className="text-[10px]">Default</Badge>}
        </span>
        {template.description?.trim() ? (
          <span className="line-clamp-1 text-xs text-muted-foreground">
            <MarkdownView inline>{template.description}</MarkdownView>
          </span>
        ) : null}
      </span>
      {meta !== undefined && (
        <span className="shrink-0 whitespace-nowrap text-xs text-muted-foreground">{meta}</span>
      )}
    </>
  );
}

/** The row layout without being a button — for a list whose action lives in
 * `meta` (Copy from catalog's Copy button). A button in a button is not HTML. */
export function TemplateStaticRow({
  template,
  keyBadge,
  meta,
  className,
}: {
  template: TemplateFace;
  keyBadge?: React.ReactNode;
  meta?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn(ROW_CLASSES, className)}>
      <RowInner template={template} keyBadge={keyBadge} meta={meta} />
    </div>
  );
}

export function TemplateCard({
  template,
  variant = "card",
  selected = false,
  disabled = false,
  meta,
  keyBadge,
  className,
  onClick,
  ...rest
}: {
  template: TemplateFace;
  variant?: "card" | "row";
  selected?: boolean;
  disabled?: boolean;
  /** Right-hand slot on the row variant — "current", "17 of 17 files". */
  meta?: React.ReactNode;
  /** A mono key badge beside the name (row variant), e.g. the template_key. */
  keyBadge?: React.ReactNode;
  className?: string;
  onClick?: () => void;
} & Omit<React.ComponentProps<"button">, "onClick" | "className">) {
  if (variant === "row") {
    return (
      <button
        type="button"
        disabled={disabled}
        onClick={onClick}
        className={cn(
          ROW_CLASSES,
          "outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50",
          disabled && "cursor-default text-muted-foreground",
          selected && "border-primary ring-1 ring-primary",
          !disabled && !selected && "hover:bg-muted/50",
          className,
        )}
        {...rest}
      >
        <RowInner template={template} keyBadge={keyBadge} meta={meta} />
      </button>
    );
  }

  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "relative grid w-full grid-rows-[auto_1fr] overflow-hidden rounded-md border bg-card text-left outline-none transition-[border-color,box-shadow]",
        "focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50",
        selected ? "border-primary ring-1 ring-primary" : "hover:border-ring/70",
        disabled && "cursor-default opacity-60",
        className,
      )}
      {...rest}
    >
      <span className="relative block bg-muted">
        <TemplateCover template={template} />
        {selected && (
          <span
            aria-hidden="true"
            className="absolute right-2 top-2 grid size-[22px] place-items-center rounded-full bg-primary text-primary-foreground shadow"
          >
            <Check className="size-3.5" strokeWidth={3} />
          </span>
        )}
      </span>
      <span className="grid content-start gap-1 px-3 pb-3 pt-2.5">
        <span className="flex flex-wrap items-center gap-1.5 text-sm font-semibold leading-tight">
          <span>{template.name}</span>
          {template.is_default && <Badge className="text-[10px]">Default</Badge>}
        </span>
        {template.description?.trim() ? (
          <span className="line-clamp-3 text-xs leading-snug text-muted-foreground">
            <MarkdownView inline>{template.description}</MarkdownView>
          </span>
        ) : null}
      </span>
    </button>
  );
}
