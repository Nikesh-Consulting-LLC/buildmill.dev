import { MarkdownView } from "@/components/markdown-view";
import { cn } from "@/lib/utils";

/** us-101.4: the notes declaration, rendered.
 *
 * The agent authors every word and section of this; the app decides only how
 * it looks. That split is what lets a restyle reach every release ever
 * written, and it is why this is a declaration rather than the HTML page the
 * request started as — stored HTML freezes each release in the CSS of the day
 * it was cut, and could never have carried the verdict buttons that gate
 * sign-off.
 */
export type NotesDoc = {
  standfirst?: string;
  sections?: Record<string, string>;
  blocks?: (
    | { block: "prose"; markdown: string }
    | { block: "callout"; tone: "info" | "warn" | "risk"; title: string; body: string }
  )[];
};

/** Whether there is anything here at all. A release cut before us-101.4 has
 * an empty declaration and renders its markdown notes exactly as before. */
export function hasNotesDoc(doc: NotesDoc | null | undefined): boolean {
  if (!doc) return false;
  return Boolean(
    (doc.standfirst ?? "").trim() ||
      (doc.blocks ?? []).length ||
      Object.keys(doc.sections ?? {}).length
  );
}

const TONE = {
  info: "border-l-primary bg-muted/50",
  warn: "border-l-amber-500 bg-amber-500/5",
  risk: "border-l-destructive bg-destructive/5",
} as const;

export function ReleaseNotesDoc({ doc }: { doc: NotesDoc }) {
  return (
    <div className="flex flex-col gap-4">
      {doc.standfirst?.trim() ? (
        <p className="max-w-[62ch] text-base text-muted-foreground">
          {doc.standfirst}
        </p>
      ) : null}
      {(doc.blocks ?? []).map((b, i) =>
        b.block === "callout" ? (
          <div
            key={i}
            className={cn("rounded-r-md border border-l-[3px] px-4 py-3", TONE[b.tone] ?? TONE.info)}
          >
            {b.title ? (
              <p className="text-sm font-semibold">{b.title}</p>
            ) : null}
            {b.body ? (
              <p className="mt-1 max-w-[66ch] text-sm text-muted-foreground">
                {b.body}
              </p>
            ) : null}
          </div>
        ) : (
          <MarkdownView key={i}>{b.markdown}</MarkdownView>
        )
      )}
    </div>
  );
}
