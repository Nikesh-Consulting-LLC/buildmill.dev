"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Loader2, Sparkles } from "lucide-react";
import { apiCall } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";

type Tldr = { headline: string; bullets: string[] };

/** The popup's body, shared by both buttons — one loading state, one error
 * state, one way a summary looks. */
function TldrBody({
  loading,
  noLlm,
  error,
  data,
  footnote,
  onRetry,
}: {
  loading: boolean;
  noLlm: boolean;
  error: string | null;
  data: Tldr | null;
  footnote: string;
  onRetry: () => void;
}) {
  return (
    <>
      {loading && (
        <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" />
          Summarizing…
        </div>
      )}

      {noLlm && (
        <p className="py-2 text-sm">
          No LLM provider is configured.{" "}
          <Link href="/settings/llm-providers" className="underline">
            Set one up in Settings
          </Link>
          .
        </p>
      )}

      {error && (
        <div className="py-2 text-sm">
          <p className="text-destructive">{error}</p>
          <Button variant="outline" size="sm" className="mt-2" onClick={onRetry}>
            Retry
          </Button>
        </div>
      )}

      {data && (
        <div className="flex flex-col gap-3">
          <p className="text-base font-semibold">{data.headline}</p>
          {data.bullets.length > 0 && (
            <ul className="list-disc space-y-1 pl-5 text-sm">
              {data.bullets.map((b, i) => (
                <li key={i}>{b}</li>
              ))}
            </ul>
          )}
          <p className="text-xs text-muted-foreground">{footnote}</p>
        </div>
      )}
    </>
  );
}

/** US-18.1: a one-click, summary-only TLDR of a work-item content block —
 * headline + a few bullets, from the org's configured LLM. Generated on demand
 * (not persisted); reopening regenerates.
 *
 * US-25.3: the trigger is a `DialogTrigger`. It used to be a bare Button with
 * `onClick={() => setOpen(true)}` on a *controlled* dialog, so opening it never
 * went through `onOpenChange` and the fetch it was wired to never ran — the
 * popup opened onto an empty body, which is the bug that was reported as "TLDR
 * does nothing". The load is now keyed on the open state in an effect, so
 * opening and summarizing cannot come apart again. */
export function TldrButton({
  content,
  kind,
  label = "TLDR",
}: {
  content: string | null | undefined;
  /** "story" | "prd" | "plan" | "test_plan" — sets the summary's framing. */
  kind: string;
  label?: string;
}) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<Tldr | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [noLlm, setNoLlm] = useState(false);

  const empty = !(content ?? "").trim();

  const run = useCallback(async () => {
    setLoading(true);
    setError(null);
    setNoLlm(false);
    setData(null);
    try {
      const r = (await apiCall("/api/v1/llm/tldr", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: content ?? "", kind }),
      })) as Tldr;
      setData(r);
    } catch (e) {
      const msg = (e as Error).message;
      if (/no llm provider configured/i.test(msg)) setNoLlm(true);
      else setError(msg);
    } finally {
      setLoading(false);
    }
  }, [content, kind]);

  useEffect(() => {
    if (open) run();
  }, [open, run]);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger
        disabled={empty}
        title={empty ? "Nothing to summarize yet" : "Summarize this in a few words"}
        render={
          <Button variant="outline" size="sm" className="h-7 gap-1 px-2 text-xs" />
        }
      >
        <Sparkles className="size-3" />
        {label}
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>TLDR</DialogTitle>
          <DialogDescription>A short summary — nothing added.</DialogDescription>
        </DialogHeader>
        <TldrBody
          loading={loading}
          noLlm={noLlm}
          error={error}
          data={data}
          footnote="Summary only — regenerated each time you open this."
          onRetry={run}
        />
      </DialogContent>
    </Dialog>
  );
}

type TldrState =
  | { status: "ready"; summary: Tldr; generated_at: string | null }
  | { status: "generating" }
  | { status: "failed"; error: string }
  | { status: "empty"; detail: string };

const POLL_MS = 2000;
// ~2 minutes. Past that the honest answer is "this isn't coming", not a
// spinner that outlasts the manager's attention.
const MAX_POLLS = 60;

/** US-25.3: TLDR of the WHOLE work item, not of one content block.
 *
 * A manager opening an item they have not read in a week is asking "what is
 * this", and the story text alone leaves out the plan they are about to
 * approve. A feature summarizes its description and approved PRD; a story its
 * text, acceptance criteria, approved plan and instruction set.
 *
 * The summary is stored on the item and generated out of band, so this never
 * waits on a model inside a request: it polls a cheap endpoint that answers
 * `generating` until the stored summary lands. Unchanged content is served
 * straight from storage with no second LLM call. */
export function WorkItemTldrButton({
  issueId,
  isFeature,
}: {
  issueId: string;
  /** Only changes the popup's wording — the server decides scope from the
   * item's own type, so the two can never disagree about what was read. */
  isFeature: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<TldrState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [noLlm, setNoLlm] = useState(false);
  const polls = useRef(0);

  const load = useCallback(
    async (retry: boolean): Promise<TldrState | null> => {
      const r = (await apiCall(
        `/api/v1/llm/work-items/${issueId}/tldr${retry ? "?retry=true" : ""}`
      )) as TldrState;
      return r;
    },
    [issueId]
  );

  const start = useCallback(
    (retry: boolean) => {
      let cancelled = false;
      polls.current = 0;
      setError(null);
      setNoLlm(false);
      setState(null);

      async function tick(isRetry: boolean) {
        try {
          const r = await load(isRetry);
          if (cancelled || !r) return;
          if (r.status === "generating") {
            polls.current += 1;
            if (polls.current > MAX_POLLS) {
              setError(
                "The summary is taking longer than expected. Try again in a moment."
              );
              return;
            }
            setState(r);
            setTimeout(() => tick(false), POLL_MS);
            return;
          }
          if (r.status === "failed") {
            if (/no llm provider configured/i.test(r.error)) setNoLlm(true);
            else setError(r.error);
            return;
          }
          setState(r);
        } catch (e) {
          if (cancelled) return;
          const msg = (e as Error).message;
          if (/no llm provider configured/i.test(msg)) setNoLlm(true);
          else setError(msg);
        }
      }

      tick(retry);
      return () => {
        cancelled = true;
      };
    },
    [load]
  );

  useEffect(() => {
    if (!open) return;
    return start(false);
  }, [open, start]);

  const ready = state?.status === "ready" ? state.summary : null;
  const empty = state?.status === "empty" ? state.detail : null;

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger
        title="Summarize this whole work item"
        render={
          <Button variant="outline" size="sm" className="h-7 gap-1 px-2 text-xs" />
        }
      >
        <Sparkles className="size-3" />
        TLDR
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>TLDR</DialogTitle>
          <DialogDescription>
            {isFeature
              ? "This feature's description and approved PRD."
              : "This story, its acceptance criteria, plan and instructions."}
          </DialogDescription>
        </DialogHeader>
        {empty ? (
          <p className="py-2 text-sm text-muted-foreground">{empty}</p>
        ) : (
          <TldrBody
            loading={!ready && !error && !noLlm}
            noLlm={noLlm}
            error={error}
            data={ready}
            footnote="Summary only — stored, and regenerated when the item changes."
            onRetry={() => start(true)}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}
