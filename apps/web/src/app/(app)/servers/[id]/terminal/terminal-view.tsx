"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, Plug, RotateCcw, ShieldAlert } from "lucide-react";
import "@xterm/xterm/css/xterm.css";
import { apiCall, API_WS_URL, getAccessToken } from "@/lib/api";
import { Button } from "@/components/ui/button";

type Status = "connecting" | "connected" | "closed" | "error";

// Minimal shapes for the xterm objects we hold across renders.
type XTerm = {
  open: (el: HTMLElement) => void;
  write: (data: string | Uint8Array) => void;
  onData: (cb: (data: string) => void) => void;
  loadAddon: (addon: unknown) => void;
  dispose: () => void;
  cols: number;
  rows: number;
  focus: () => void;
};
type Fit = { fit: () => void };

export function TerminalView({
  serverId,
  agentSlotId,
}: {
  serverId: string;
  /** US-55.6: when set, the server drops the session straight into that
   * agent slot's own `claude` session (its OS user, its workspace) instead
   * of a plain shell as the registered machine login. */
  agentSlotId?: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<XTerm | null>(null);
  const fitRef = useRef<Fit | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const [status, setStatus] = useState<Status>("connecting");
  const [message, setMessage] = useState<string>("");
  const [errorCode, setErrorCode] = useState<string | null>(null);
  const [retrusting, setRetrusting] = useState(false);
  const [attempt, setAttempt] = useState(0);

  // Fit only when the container is actually laid out. A pop-up window that is
  // still opening can report a 0 (or briefly huge) size; fitting then leaves
  // xterm wedged. Returns true if a real fit happened.
  const safeFit = useCallback((): boolean => {
    const el = containerRef.current;
    const fit = fitRef.current;
    if (!el || !fit) return false;
    if (el.clientWidth < 8 || el.clientHeight < 8) return false;
    try {
      fit.fit();
      return true;
    } catch {
      return false;
    }
  }, []);

  const connect = useCallback(async () => {
    const term = termRef.current;
    const fit = fitRef.current;
    if (!term || !fit) return;

    setStatus("connecting");
    setMessage("");
    setErrorCode(null);

    let token: string;
    try {
      token = await getAccessToken();
    } catch {
      setStatus("error");
      setMessage("You're signed out. Refresh and sign in again.");
      return;
    }

    const ws = new WebSocket(`${API_WS_URL}/api/v1/servers/${serverId}/terminal`);
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;

    // A handshake the server never answers leaves the socket CONNECTING until
    // the browser's ~4-minute timeout; surface a retryable error long before.
    const connectTimer = window.setTimeout(() => {
      if (ws.readyState === WebSocket.CONNECTING) {
        setStatus("error");
        setMessage("Could not reach the terminal service. Try reconnecting.");
        ws.close();
      }
    }, 15000);

    ws.onopen = () => {
      window.clearTimeout(connectTimer);
      safeFit();
      const cols = term.cols > 0 ? term.cols : 80;
      const rows = term.rows > 0 ? term.rows : 24;
      ws.send(JSON.stringify({ token, cols, rows, agentSlotId }));
    };

    ws.onmessage = (event) => {
      if (typeof event.data !== "string") {
        term.write(new Uint8Array(event.data as ArrayBuffer));
        return;
      }
      let msg: { type?: string; message?: string; reason?: string; code?: string };
      try {
        msg = JSON.parse(event.data);
      } catch {
        return;
      }
      if (msg.type === "ready") {
        setStatus("connected");
        term.focus();
      } else if (msg.type === "error") {
        setStatus("error");
        setMessage(msg.message ?? "Connection failed.");
        setErrorCode(msg.code ?? null);
      } else if (msg.type === "closed") {
        term.write(`\r\n\x1b[33m${msg.reason ?? "Connection closed."}\x1b[0m\r\n`);
      }
    };

    ws.onclose = () => {
      setStatus((s) => (s === "error" ? s : "closed"));
      if (termRef.current) {
        termRef.current.write("\r\n\x1b[90mconnection closed\x1b[0m\r\n");
      }
    };
    ws.onerror = () => {
      setStatus((s) => (s === "connected" ? "closed" : "error"));
    };

    term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "input", data }));
      }
    });
  }, [serverId, agentSlotId, safeFit]);

  // Initialise xterm once, then connect.
  useEffect(() => {
    let disposed = false;
    let resizeObs: ResizeObserver | null = null;

    (async () => {
      const { Terminal } = await import("@xterm/xterm");
      const { FitAddon } = await import("@xterm/addon-fit");
      const { CanvasAddon } = await import("@xterm/addon-canvas");
      if (disposed || !containerRef.current) return;

      const term = new Terminal({
        cursorBlink: true,
        fontFamily:
          'ui-monospace, SFMono-Regular, Menlo, Monaco, "Cascadia Code", monospace',
        fontSize: 13,
        theme: { background: "#0a0a0a", foreground: "#e5e5e5" },
      }) as unknown as XTerm;
      const fit = new FitAddon() as unknown as Fit;
      term.loadAddon(fit);
      // xterm's default DOM renderer updates a live DOM node per changed
      // cell — fine for a quiet shell, but Claude Code's TUI redraws
      // constantly (spinners, live input echo) and that contends with the
      // same main thread that processes your keystrokes, showing up as
      // typing lag. The canvas renderer batches redraws onto a <canvas>
      // instead of touching the DOM per frame.
      try {
        term.loadAddon(new CanvasAddon() as unknown as { activate?: unknown });
      } catch {
        // Best-effort — a browser without canvas 2D context support (rare)
        // just keeps the default DOM renderer instead of failing to open.
      }
      termRef.current = term;
      fitRef.current = fit;
      term.open(containerRef.current);
      // Fit after layout settles rather than synchronously on open — in a
      // still-opening pop-up window the container isn't measurable yet.
      requestAnimationFrame(() => safeFit());

      resizeObs = new ResizeObserver(() => {
        if (!safeFit()) return;
        const ws = wsRef.current;
        const t = termRef.current;
        if (ws && ws.readyState === WebSocket.OPEN && t && t.cols > 0 && t.rows > 0) {
          ws.send(JSON.stringify({ type: "resize", cols: t.cols, rows: t.rows }));
        }
      });
      resizeObs.observe(containerRef.current);

      connect();
    })();

    return () => {
      disposed = true;
      resizeObs?.disconnect();
      wsRef.current?.close();
      termRef.current?.dispose();
      termRef.current = null;
      fitRef.current = null;
    };
    // Reconnect fully re-runs this effect via `attempt`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attempt]);

  function disconnect() {
    wsRef.current?.send(JSON.stringify({ type: "disconnect" }));
    wsRef.current?.close();
    setStatus("closed");
  }

  function reconnect() {
    wsRef.current?.close();
    setAttempt((a) => a + 1);
  }

  async function trustAndReconnect() {
    setRetrusting(true);
    try {
      await apiCall(`/api/v1/servers/${serverId}/trust-host-key`, { method: "POST" });
      reconnect();
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setRetrusting(false);
    }
  }

  return (
    <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border bg-[#0a0a0a]">
      <div className="flex items-center justify-between border-b border-white/10 px-3 py-1.5">
        <span className="flex items-center gap-2 text-xs text-white/60">
          {status === "connecting" && <Loader2 className="size-3.5 animate-spin" />}
          {status === "connecting"
            ? "Connecting…"
            : status === "connected"
              ? "Connected"
              : status === "closed"
                ? "Disconnected"
                : "Connection failed"}
        </span>
        <div className="flex items-center gap-1.5">
          {(status === "closed" || status === "error") && (
            <Button
              size="sm"
              variant="ghost"
              className="h-7 text-white/80 hover:bg-white/10 hover:text-white"
              onClick={reconnect}
            >
              <RotateCcw className="size-3.5" />
              Reconnect
            </Button>
          )}
          {status === "connected" && (
            <Button
              size="sm"
              variant="ghost"
              className="h-7 text-white/80 hover:bg-white/10 hover:text-white"
              onClick={disconnect}
            >
              <Plug className="size-3.5" />
              Disconnect
            </Button>
          )}
        </div>
      </div>
      {status === "error" && message && (
        <div className="flex flex-col gap-2 border-b border-destructive/40 bg-destructive/10 px-3 py-2 text-xs font-medium text-red-300">
          <span className="flex items-center gap-1.5">
            {errorCode === "host_key_changed" && <ShieldAlert className="size-3.5" />}
            {message}
          </span>
          {errorCode === "host_key_changed" && (
            <Button
              size="sm"
              variant="ghost"
              className="h-7 w-fit text-red-200 hover:bg-white/10 hover:text-white"
              disabled={retrusting}
              onClick={trustAndReconnect}
            >
              {retrusting && <Loader2 className="size-3.5 animate-spin" />}
              Trust new host key & reconnect
            </Button>
          )}
        </div>
      )}
      <div ref={containerRef} className="min-h-0 flex-1 p-2" />
    </div>
  );
}
