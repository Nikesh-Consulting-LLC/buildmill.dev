"use client";

/**
 * US-78.8: the manager's console onto a live interactive run.
 *
 * Deliberately NOT an xterm. The server terminal (`servers/[id]/terminal`) is a
 * PTY and xterm is exactly right for it; this is a stream of typed ACP events —
 * message, thought, tool call, plan — and rendering them as raw terminal bytes
 * would throw away the structure that makes them readable. The reconnect and
 * auth handshake are borrowed from that component; the rendering is not.
 *
 * US-88.1: it still *looks* like that terminal, because it is one. Same dark
 * surface, same thin chrome, mono throughout — and fixed at that, not
 * theme-following: a terminal is a terminal in a light room too. What the
 * structure buys over raw bytes is spent here, in the gutter: one glyph and one
 * ANSI-ish colour per event kind, so a forty-line session is scannable without
 * reading it. Wrapped text hang-indents past that glyph so the column stays a
 * column.
 *
 * And most of a turn is working-out, so most of a turn is folded: consecutive
 * working rows collapse into one grey line titled by whatever the agent is
 * doing now (see `console-blocks.ts`), open on a click, and turn a star while
 * the fold is the live tail. Left flat, twenty rows of tool calls and
 * permission notices push the sentence the manager came to read off the top of
 * the screen — the answer arriving is exactly when the working-out stops being
 * interesting.
 *
 * Attaching is read-only until the manager types. Detaching leaves the run
 * going: the session lives on the pool machine, not in this tab.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronRight, Send, Square, TerminalSquare } from "lucide-react";

import { Button } from "@/components/ui/button";
import { API_WS_URL } from "@/lib/api";
import { createClient } from "@/lib/supabase/client";
import { cn } from "@/lib/utils";
import { TerminalMarkdown } from "./terminal-markdown";
import { groupTitle, toBlocks, type Line } from "./console-blocks";

/** The kinds whose content the agent *wrote*, and therefore wrote in markdown.
 *  Everything else is a line the runner composed — a tool call, a plan
 *  counter, a progress note, the manager's own words — where a stray asterisk
 *  in a file path is an asterisk, not emphasis. */
const AUTHORED = new Set(["output", "step"]);

/** The `run_trace` kinds (migration 118's permitted set) plus `you` — the one
 *  kind the server never sends, because it is the manager's own typed line.
 *  Glyph and colour together, so a kind is identifiable at a glance and still
 *  legible to anyone who cannot tell the colours apart. */
const KIND: Record<string, { glyph: string; text: string; gutter: string }> = {
  you: { glyph: "❯", text: "text-emerald-300", gutter: "text-emerald-400" },
  output: { glyph: "⏺", text: "text-neutral-100", gutter: "text-neutral-500" },
  step: { glyph: "✻", text: "italic text-neutral-400", gutter: "text-neutral-600" },
  tool: { glyph: "▸", text: "text-cyan-300", gutter: "text-cyan-500" },
  decision: { glyph: "◆", text: "text-amber-300", gutter: "text-amber-500" },
  error: { glyph: "✖", text: "text-red-400", gutter: "text-red-500" },
  progress: { glyph: "·", text: "text-neutral-500", gutter: "text-neutral-600" },
};
const FALLBACK = KIND.progress;

/** One transcript row: the gutter glyph, then the content — markdown where the
 *  agent wrote it, raw where the runner composed it. */
function Row({ line }: { line: Line }) {
  const k = KIND[line.kind] ?? FALLBACK;
  return (
    <div className="grid grid-cols-[0.85rem_1fr] gap-2">
      <span className={cn("select-none text-right", k.gutter)}>{k.glyph}</span>
      {AUTHORED.has(line.kind) ? (
        <TerminalMarkdown className={k.text}>{line.content}</TerminalMarkdown>
      ) : (
        <span className={cn("min-w-0 whitespace-pre-wrap break-words", k.text)}>
          {line.content}
        </span>
      )}
    </div>
  );
}

/** US-88.1: a run of working rows — thinking, tool calls, permission notices,
 *  progress — as one grey line that says what the agent is doing *now*, and
 *  opens on a click.
 *
 *  Collapsed by default, because the working-out is how the answer was reached
 *  and the answer is what the manager came for. The title tracks the newest row
 *  in the group rather than the first, so a fold that is still filling reads as
 *  a live status line instead of a stale one.
 *
 *  `live` — this fold is the tail of an attached session, so it is not a record
 *  of work, it *is* the work, still happening. The star turns. */
function WorkingGroup({
  lines,
  open,
  live,
  onToggle,
}: {
  lines: Line[];
  open: boolean;
  live: boolean;
  onToggle: () => void;
}) {
  const Chevron = open ? ChevronDown : ChevronRight;
  return (
    <div className={cn(open && "my-0.5 border-l border-white/10 pl-2")}>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        aria-busy={live}
        className="grid w-full grid-cols-[0.85rem_1fr] gap-2 text-left"
      >
        <span
          className={cn(
            "select-none text-right",
            live
              ? "inline-block animate-[spin_2.4s_linear_infinite] text-neutral-300"
              : "text-neutral-600",
          )}
        >
          ✻
        </span>
        <span
          className={cn(
            "flex min-w-0 items-center gap-1 hover:text-neutral-300",
            live ? "text-neutral-400" : "text-neutral-500",
          )}
        >
          <Chevron className="size-3 shrink-0 text-neutral-600" />
          <span className="truncate italic">{groupTitle(lines)}</span>
          {lines.length > 1 && (
            <span className="shrink-0 text-neutral-600">· {lines.length} steps</span>
          )}
          {live && <span className="shrink-0 text-neutral-600">…</span>}
        </span>
      </button>
      {open && (
        <div className="mt-0.5 flex flex-col gap-0.5">
          {lines.map((line, i) => (
            <Row key={i} line={line} />
          ))}
        </div>
      )}
    </div>
  );
}

export function InteractiveConsole({
  runId,
  scope = "run",
  title,
  label,
  fill = false,
}: {
  runId: string;
  /** US-78.10: the same console over a session instead of a run. One component
   *  and one socket protocol — a session is a different owner of the
   *  conversation, not a different kind of conversation. */
  scope?: "run" | "session";
  /** What the chrome bar calls this window. The agent's name where the caller
   *  knows it; otherwise the generic label. */
  title?: string;
  /** US-88.1: what this window *is*, in the chrome bar — "live session", "last
   *  session", "run console". It lives here rather than in a paragraph above
   *  the console because that paragraph cost more screen than it was worth. */
  label?: string;
  /** US-88.1: fill the height the parent gives instead of sizing to a window
   *  of its own. On a page that is nothing but this console, a terminal that
   *  grows into the screen as output arrives is a terminal that was too small
   *  when you opened it. Off by default — inside the run page it is one card
   *  among many and must not eat the page. */
  fill?: boolean;
}) {
  const [lines, setLines] = useState<Line[]>([]);
  const [steerable, setSteerable] = useState(false);
  const [state, setState] = useState<"connecting" | "attached" | "closed">("connecting");
  const [note, setNote] = useState<string | null>(null);
  const [text, setText] = useState("");
  // US-88.1: which working groups the manager has opened, by the index of the
  // group's first line. Held here rather than inside the group so a re-render
  // — one arrives per streamed line — cannot close what was opened.
  const [opened, setOpened] = useState<Set<number>>(new Set());
  const socket = useRef<WebSocket | null>(null);
  const scroller = useRef<HTMLDivElement | null>(null);
  // US-88.1 AC8: follow the tail only from the tail. Scrolling up to read the
  // scrollback while the agent streams used to yank the view back down on
  // every line, which made reading a running session impossible.
  const stick = useRef(true);

  useEffect(() => {
    let closed = false;
    let ws: WebSocket | null = null;

    (async () => {
      const supabase = createClient();
      const { data } = await supabase.auth.getSession();
      const token = data.session?.access_token;
      if (!token || closed) return;

      // `API_WS_URL` is the shared helper every other socket in this app uses.
      // A local `process.env.NEXT_PUBLIC_API_URL ?? ""` was wrong twice over:
      // it duplicated this, and its empty fallback made the URL relative, which
      // `new WebSocket()` rejects outright — so with the variable unset the
      // console failed at construction and never even reported a state.
      const path =
        scope === "session"
          ? `/api/v1/runs/sessions/${runId}/console`
          : `/api/v1/runs/${runId}/console`;
      ws = new WebSocket(`${API_WS_URL}${path}`);
      socket.current = ws;

      ws.onopen = () => ws?.send(JSON.stringify({ token }));
      ws.onmessage = (event) => {
        const msg = JSON.parse(event.data as string);
        if (msg.type === "attached") {
          setState("attached");
          setSteerable(!!msg.steerable);
          setLines(
            (msg.trace ?? []).map((t: Line) => ({ kind: t.kind, content: t.content })),
          );
          return;
        }
        if (msg.type === "trace") {
          setLines((prev) => [...prev, { kind: msg.kind, content: msg.content }]);
          return;
        }
        if (msg.type === "refused" || msg.type === "error") {
          setNote(msg.message);
        }
      };
      ws.onclose = () => {
        if (!closed) setState("closed");
      };
    })();

    return () => {
      closed = true;
      ws?.close();
      socket.current = null;
    };
  }, [runId, scope]);

  const blocks = useMemo(() => toBlocks(lines), [lines]);

  // Follow the tail, the way a console should — unless the reader has left it.
  useEffect(() => {
    const el = scroller.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [lines]);

  const send = useCallback(
    (action: "prompt" | "cancel", body?: string) => {
      const ws = socket.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        setNote("The console is not connected.");
        return;
      }
      setNote(null);
      ws.send(JSON.stringify({ action, text: body }));
      if (action === "prompt" && body) {
        // Shown immediately as the manager's own line — the agent's reply
        // arrives as trace, so this is the one thing the console renders that
        // did not come from the server. Its own kind, so it can never be
        // mistaken for something the agent said.
        stick.current = true;
        setLines((prev) => [...prev, { kind: "you", content: body }]);
        setText("");
      }
    },
    [],
  );

  const status =
    state === "attached"
      ? steerable
        ? { dot: "bg-emerald-400", pulse: true, label: "attached · you can type" }
        : { dot: "bg-sky-400", pulse: false, label: "attached · read only" }
      : state === "connecting"
        ? { dot: "bg-amber-400", pulse: true, label: "connecting…" }
        : { dot: "bg-neutral-600", pulse: false, label: "disconnected" };

  return (
    <div
      className={cn(
        "overflow-hidden rounded-lg border border-white/10 bg-[#0a0a0a] font-mono",
        // `flex-1` rather than `h-full`: in a column the parent lays out, the
        // console must take what is *left* after the back link and the close
        // panel, not the whole of it.
        fill && "flex min-h-0 flex-1 flex-col",
      )}
    >
      <div className="flex shrink-0 items-center justify-between gap-3 border-b border-white/10 px-3 py-1.5 text-[11px]">
        <span className="flex min-w-0 items-center gap-2 text-white/50">
          <TerminalSquare className="size-3.5 shrink-0" />
          <span className="truncate text-white/80">{title ?? "agent"}</span>
          <span className="text-white/20">·</span>
          <span className="shrink-0">
            {label ?? (scope === "session" ? "live session" : "run console")}
          </span>
        </span>
        <span className="flex shrink-0 items-center gap-1.5 text-white/45">
          <span
            className={cn(
              "size-1.5 rounded-full",
              status.dot,
              status.pulse && "animate-pulse",
            )}
          />
          {status.label}
        </span>
      </div>

      <div
        ref={scroller}
        onScroll={(e) => {
          const el = e.currentTarget;
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
        }}
        className={cn(
          "overflow-y-auto px-3 py-2.5 text-[12px] leading-[1.55]",
          fill ? "min-h-0 flex-1" : "max-h-[32rem] min-h-[14rem]",
          "[&::-webkit-scrollbar]:w-2 [&::-webkit-scrollbar-thumb]:rounded-full",
          "[&::-webkit-scrollbar-thumb]:bg-white/15 [&::-webkit-scrollbar-track]:bg-transparent",
        )}
      >
        {lines.length === 0 ? (
          // A boot log, not an empty card — it says the same thing in the
          // voice of the surface it is on.
          <p className="whitespace-pre-wrap text-neutral-600">
            {state === "connecting"
              ? "· attaching to the agent…"
              : state === "closed"
                ? "· not connected."
                : "· attached. nothing said yet."}
          </p>
        ) : (
          <div className="flex flex-col gap-0.5">
            {blocks.map((block, i) =>
              block.type === "group" ? (
                <WorkingGroup
                  key={block.key}
                  lines={block.lines}
                  open={opened.has(block.key)}
                  // The tail of an attached session is work in progress; once
                  // the agent speaks, the answer is the tail and the fold above
                  // it is history.
                  live={state === "attached" && i === blocks.length - 1}
                  onToggle={() =>
                    setOpened((prev) => {
                      const next = new Set(prev);
                      if (!next.delete(block.key)) next.add(block.key);
                      return next;
                    })
                  }
                />
              ) : (
                <Row key={block.key} line={block.line} />
              ),
            )}
          </div>
        )}
        {/* The waiting cursor — but not under a live fold, whose turning star
            already says the agent is mid-thought. Two idle animations reads as
            a glitch. */}
        {state === "attached" &&
          lines.length > 0 &&
          blocks[blocks.length - 1]?.type !== "group" && (
            <div className="grid grid-cols-[0.85rem_1fr] gap-2 pt-0.5">
              <span />
              <span className="inline-block h-[0.95em] w-[0.5em] animate-pulse bg-neutral-500 align-text-bottom" />
            </div>
          )}
      </div>

      {note && (
        <p className="shrink-0 border-t border-amber-400/25 bg-amber-400/10 px-3 py-1.5 text-[11px] text-amber-300">
          {note}
        </p>
      )}

      <form
        className="flex shrink-0 items-center gap-2 border-t border-white/10 px-3 py-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (text.trim()) send("prompt", text.trim());
        }}
      >
        <span
          aria-hidden
          className={cn(
            "select-none text-sm",
            steerable ? "text-emerald-400" : "text-neutral-700",
          )}
        >
          ❯
        </span>
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={!steerable}
          spellCheck={false}
          autoComplete="off"
          aria-label="Send a message to the agent"
          placeholder={
            steerable
              ? "type to the agent — it reads this as its next turn"
              : "this run is not accepting input"
          }
          className={cn(
            "min-w-0 flex-1 bg-transparent text-[12px] text-neutral-100 caret-emerald-400",
            "outline-none placeholder:text-neutral-600",
            "disabled:cursor-not-allowed disabled:placeholder:text-neutral-700",
          )}
        />
        <Button
          type="submit"
          size="sm"
          variant="ghost"
          disabled={!steerable || !text.trim()}
          className="h-7 text-[11px] text-emerald-300 hover:bg-emerald-400/10 hover:text-emerald-200 disabled:text-neutral-700"
        >
          <Send className="size-3.5" />
          Send
        </Button>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          disabled={!steerable}
          onClick={() => send("cancel")}
          className="h-7 text-[11px] text-white/70 hover:bg-white/10 hover:text-white disabled:text-neutral-700"
        >
          <Square className="size-3.5" />
          Stop
        </Button>
      </form>
    </div>
  );
}
