"use client";

import { useEffect, useState } from "react";
import ReactMarkdown, { defaultUrlTransform, type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { ImageOff } from "lucide-react";
import { cn } from "@/lib/utils";
import { createClient } from "@/lib/supabase/client";

/** US-5.15: the single markdown renderer. Every read view and the editor's
 * Preview tab go through this component, so GFM (tables, task lists,
 * strikethrough) renders everywhere and preview is exactly what the saved
 * item will show.
 *
 * US-5.16: `attachment://<path>` image sources resolve to short-lived
 * signed URLs from the private `attachments` bucket at render time, under
 * the viewer's own RLS — stored markdown never contains expiring URLs. */

const MD_PROSE =
  // US-35.8: `w-0 min-w-full` makes the markdown root shrink to whatever space
  // it is given instead of growing to its widest line. Fixing this here rather
  // than at each of the ~30 call sites is deliberate: the alternative is a
  // `min-w-0` every caller must remember, and the ones that forget are exactly
  // the surfaces nobody tests on a tablet.
  "w-0 min-w-full " +
  "prose prose-sm dark:prose-invert max-w-none text-sm leading-relaxed " +
  "[&_a]:underline [&_code]:rounded [&_code]:bg-muted [&_code]:px-1 " +
  "[&_h1]:text-lg [&_h2]:text-base [&_h3]:text-sm [&_li]:my-0.5 " +
  "[&_ol]:list-decimal [&_ol]:pl-5 [&_p]:my-2 [&_ul]:list-disc [&_ul]:pl-5 " +
  // US-35.8: a fenced code block is the widest thing markdown produces, and it
  // had no scroll container — a single long line pushed the whole page sideways
  // on a tablet, on every surface that renders markdown (plans, PRDs,
  // guidelines, learnings, agent notes). It scrolls itself now.
  "[&_pre]:overflow-x-auto [&_pre]:rounded-md [&_pre]:bg-muted [&_pre]:p-3 " +
  "[&_pre_code]:bg-transparent " +
  // Inline code carries ids, URLs and hashes with no spaces to break on; only
  // inline, never inside a `pre`, where breaking would corrupt the listing.
  "[&_:not(pre)>code]:break-words " +
  "[&_blockquote]:border-l-2 [&_blockquote]:pl-3 [&_blockquote]:text-muted-foreground " +
  // US-35.8: a markdown table has no wrapper to scroll, so it gets one of its
  // own — `block` makes the table itself the scroll container rather than
  // widening its parent.
  "[&_table]:block [&_table]:max-w-full [&_table]:overflow-x-auto " +
  "[&_table]:border-collapse [&_th]:border [&_th]:border-border [&_th]:px-2.5 " +
  "[&_th]:py-1.5 [&_th]:text-left [&_td]:border [&_td]:border-border " +
  "[&_td]:px-2.5 [&_td]:py-1.5 [&_del]:text-muted-foreground " +
  "[&_ul.contains-task-list]:list-none [&_ul.contains-task-list]:pl-1 " +
  "[&_input[type=checkbox]]:mr-1.5 [&_input[type=checkbox]]:align-middle " +
  "[&_img]:max-w-full [&_img]:rounded-md";

export const ATTACHMENT_SCHEME = "attachment://";
const SIGNED_URL_TTL_SECONDS = 3600;

/** Per-path signed-URL cache so a thread of comments doesn't re-sign the
 * same image repeatedly. Module-level: shared across every MarkdownView
 * on the page, kept until well before the URL would expire. */
const signedUrlCache = new Map<
  string,
  { promise: Promise<string | null>; expiresAt: number }
>();

function resolveAttachment(path: string): Promise<string | null> {
  const cached = signedUrlCache.get(path);
  if (cached && cached.expiresAt > Date.now()) return cached.promise;

  const promise = createClient()
    .storage.from("attachments")
    .createSignedUrl(path, SIGNED_URL_TTL_SECONDS)
    .then(({ data, error }) => (error ? null : (data?.signedUrl ?? null)))
    .catch(() => null);
  signedUrlCache.set(path, {
    promise,
    // refresh well before the signature actually lapses
    expiresAt: Date.now() + (SIGNED_URL_TTL_SECONDS - 300) * 1000,
  });
  return promise;
}

function AttachmentImage(props: React.ComponentProps<"img">) {
  const { src, alt, ...rest } = props;
  const isAttachment =
    typeof src === "string" && src.startsWith(ATTACHMENT_SCHEME);
  const path = isAttachment
    ? (src as string).slice(ATTACHMENT_SCHEME.length)
    : null;
  const [resolved, setResolved] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!path) return;
    let cancelled = false;
    resolveAttachment(path).then((url) => {
      if (cancelled) return;
      if (url) setResolved(url);
      else setFailed(true);
    });
    return () => {
      cancelled = true;
    };
  }, [path]);

  if (!isAttachment) {
    // eslint-disable-next-line @next/next/no-img-element
    return <img src={src} alt={alt ?? ""} className="max-h-96" {...rest} />;
  }
  if (failed) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-md border border-dashed px-2 py-1 text-xs text-muted-foreground">
        <ImageOff className="size-3.5" />
        {alt || "image unavailable"}
      </span>
    );
  }
  if (!resolved) {
    return (
      <span className="inline-block h-24 w-40 animate-pulse rounded-md bg-muted" />
    );
  }
  // eslint-disable-next-line @next/next/no-img-element
  return (
    <img
      src={resolved}
      alt={alt ?? ""}
      className="max-h-96 object-contain"
      {...rest}
    />
  );
}

/** US-118.1: the subset of markdown a template card can hold. Block
 * structure collapses to running text, links render as their text (a link
 * inside a radio card is a trap), images render nothing; bold, italic,
 * strikethrough and inline code keep their look. The caller clamps. */
const Flat = ({ children }: { children?: React.ReactNode }) => <>{children} </>;
const Gone = () => null;
const INLINE_COMPONENTS: Components = {
  p: Flat,
  h1: Flat,
  h2: Flat,
  h3: Flat,
  h4: Flat,
  h5: Flat,
  h6: Flat,
  ul: Flat,
  ol: Flat,
  li: Flat,
  blockquote: Flat,
  pre: Flat,
  table: Flat,
  thead: Flat,
  tbody: Flat,
  tr: Flat,
  th: Flat,
  td: Flat,
  br: () => <> </>,
  hr: Gone,
  img: Gone,
  a: ({ children }) => <>{children}</>,
  code: ({ children }) => (
    <code className="rounded bg-muted px-1 text-[0.9em]">{children}</code>
  ),
  strong: ({ children }) => <strong className="font-semibold text-foreground">{children}</strong>,
};

export function MarkdownView({
  children,
  className,
  inline = false,
}: {
  children: string;
  className?: string;
  /** Render as running text with no block structure — for a card, a table
   * cell, a one-line summary. Wrap in a `line-clamp-*` container to clamp. */
  inline?: boolean;
}) {
  if (inline) {
    return (
      <div className={cn("min-w-0", className)}>
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={INLINE_COMPONENTS}
          urlTransform={(url) =>
            url.startsWith(ATTACHMENT_SCHEME) ? url : defaultUrlTransform(url)
          }
        >
          {children}
        </ReactMarkdown>
      </div>
    );
  }
  return (
    <div className={cn(MD_PROSE, className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{ img: AttachmentImage }}
        urlTransform={(url) =>
          url.startsWith(ATTACHMENT_SCHEME) ? url : defaultUrlTransform(url)
        }
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
