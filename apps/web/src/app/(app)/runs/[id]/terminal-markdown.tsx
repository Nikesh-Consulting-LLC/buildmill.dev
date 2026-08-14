"use client";

/**
 * US-88.1: markdown, rendered the way a terminal renders it.
 *
 * Agents write markdown — headings, tables, backticked paths, numbered steps —
 * and a console that shows the source characters makes the manager parse
 * `**Overall**` by eye. Every other read surface in the app goes through
 * `MarkdownView`; this one deliberately does not, for two reasons that are
 * about the surface rather than the syntax:
 *
 *   - `MarkdownView` is prose. It styles with the app's tokens (`bg-muted`,
 *     `border-border`) and Tailwind's `prose` scale, both of which follow the
 *     light/dark theme. The console is a fixed black terminal, so those tokens
 *     would render a light card inside it in day mode. What belongs here is a
 *     terminal's own scale: 12px mono, tight leading, thin rules.
 *   - It resolves `attachment://` images to signed URLs and renders remote
 *     ones. A console is streaming untrusted agent output; it has no business
 *     fetching an image because a line of that output asked it to. Images
 *     render as their alt text here, and nothing is requested.
 *
 * The parser is the same one (`react-markdown` + `remark-gfm`), so what the
 * console understands and what the run page understands never diverge.
 */

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { cn } from "@/lib/utils";
import { withHardBreaks } from "./console-blocks";

export function TerminalMarkdown({
  children,
  className,
}: {
  children: string;
  className?: string;
}) {
  return (
    <div className={cn("min-w-0", className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children }) => (
            <div className="mt-2 mb-1 border-b border-white/15 pb-0.5 font-bold text-neutral-50 first:mt-0">
              {children}
            </div>
          ),
          h2: ({ children }) => (
            <div className="mt-2 mb-1 font-bold text-neutral-50 first:mt-0">
              {children}
            </div>
          ),
          h3: ({ children }) => (
            <div className="mt-1.5 mb-0.5 font-semibold text-neutral-200 first:mt-0">
              {children}
            </div>
          ),
          h4: ({ children }) => (
            <div className="mt-1.5 mb-0.5 font-semibold text-neutral-300 first:mt-0">
              {children}
            </div>
          ),
          p: ({ children }) => <p className="my-1 first:mt-0 last:mb-0">{children}</p>,
          strong: ({ children }) => (
            <strong className="font-bold text-neutral-50">{children}</strong>
          ),
          em: ({ children }) => <em className="italic">{children}</em>,
          del: ({ children }) => (
            <del className="text-neutral-600 line-through">{children}</del>
          ),
          a: ({ href, children }) => (
            <a
              href={href}
              target="_blank"
              rel="noreferrer noopener"
              className="text-cyan-300 underline underline-offset-2 hover:text-cyan-200"
            >
              {children}
            </a>
          ),
          // Inline code and fenced blocks share the `code` slot; the fenced one
          // arrives wrapped in `pre`, which supplies its own frame.
          code: ({ className: cls, children }) =>
            cls?.startsWith("language-") ? (
              <code className="block">{children}</code>
            ) : (
              <code className="rounded bg-white/10 px-1 py-px break-words text-cyan-200">
                {children}
              </code>
            ),
          pre: ({ children }) => (
            <pre className="my-1.5 overflow-x-auto rounded border border-white/10 bg-white/[0.06] p-2 text-[11.5px] leading-[1.5] text-neutral-200">
              {children}
            </pre>
          ),
          ul: ({ children }) => (
            <ul className="my-1 ml-4 list-disc marker:text-neutral-600">{children}</ul>
          ),
          ol: ({ children }) => (
            <ol className="my-1 ml-5 list-decimal marker:text-neutral-600">{children}</ol>
          ),
          li: ({ children }) => <li className="my-0.5 pl-0.5">{children}</li>,
          blockquote: ({ children }) => (
            <blockquote className="my-1 border-l-2 border-white/20 pl-2 text-neutral-400">
              {children}
            </blockquote>
          ),
          hr: () => <hr className="my-2 border-white/10" />,
          // A table is the widest thing markdown makes; it scrolls itself
          // rather than widening the console.
          table: ({ children }) => (
            <div className="my-1.5 max-w-full overflow-x-auto">
              <table className="border-collapse text-[11.5px]">{children}</table>
            </div>
          ),
          th: ({ children }) => (
            <th className="border border-white/15 bg-white/[0.06] px-2 py-0.5 text-left font-bold text-neutral-100">
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td className="border border-white/10 px-2 py-0.5 align-top">{children}</td>
          ),
          // Nothing is fetched: the alt text is the picture, in a console.
          img: ({ alt }) => (
            <span className="text-neutral-600">[image{alt ? `: ${alt}` : ""}]</span>
          ),
        }}
      >
        {withHardBreaks(children)}
      </ReactMarkdown>
    </div>
  );
}
