"use client";

import { confirmDialog } from "@/components/ui/confirm-dialog";

import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, Loader2, Save } from "lucide-react";
import { apiCall, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { baseName } from "./path-utils";

type Meta = { eol: "lf" | "crlf"; mtime: number; size: number };

export function FileEditor({
  serverId,
  path,
  justCreated,
  onClose,
}: {
  serverId: string;
  path: string;
  justCreated?: boolean;
  onClose: () => void;
}) {
  const [loading, setLoading] = useState(true);
  const [content, setContent] = useState("");
  const [meta, setMeta] = useState<Meta | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notEditable, setNotEditable] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const gutterRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let active = true;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const data = await apiCall(
          `/api/v1/servers/${serverId}/files/read?path=${encodeURIComponent(path)}`
        );
        if (!active) return;
        setContent(data.content);
        setMeta({ eol: data.eol, mtime: data.mtime, size: data.size });
      } catch (e) {
        if (!active) return;
        if (e instanceof ApiError && e.status === 422) {
          setNotEditable(e.message);
        } else {
          setError((e as Error).message);
        }
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [serverId, path]);

  // Warn on tab close / navigation while there are unsaved edits.
  useEffect(() => {
    function beforeUnload(e: BeforeUnloadEvent) {
      if (dirty) {
        e.preventDefault();
        e.returnValue = "";
      }
    }
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, [dirty]);

  const save = useCallback(
    async (forceInitial: boolean) => {
      if (!meta) return;
      setSaving(true);
      setError(null);
      let force = forceInitial;
      try {
        // Retry loop rather than self-recursion: a 409 conflict prompts once,
        // then re-attempts with force=true on confirmation.
        for (;;) {
          try {
            const result = await apiCall(`/api/v1/servers/${serverId}/files/write`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                path,
                content,
                eol: meta.eol,
                expected_mtime: meta.mtime,
                expected_size: meta.size,
                force,
              }),
            });
            setMeta({ eol: meta.eol, mtime: result.mtime, size: result.size });
            setDirty(false);
            return;
          } catch (e) {
            if (e instanceof ApiError && e.status === 409 && !force) {
              if (
                await confirmDialog({
                  title: "Overwrite server version?",
                  description: `${e.message} — overwrite the version on the server with your changes?`,
                  confirmLabel: "Overwrite",
                  destructive: true,
                })
              ) {
                force = true;
                continue;
              }
              return;
            }
            // Leave the editor content intact so nothing typed is lost.
            setError((e as Error).message);
            return;
          }
        }
      } finally {
        setSaving(false);
      }
    },
    [serverId, path, content, meta]
  );

  // Ctrl/Cmd-S to save.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        if (dirty && !saving) save(false);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [dirty, saving, save]);

  async function handleClose() {
    if (
      dirty &&
      !(await confirmDialog({
        title: "Discard changes?",
        description: "You have unsaved changes. They will be lost.",
        confirmLabel: "Discard",
        destructive: true,
      }))
    )
      return;
    onClose();
  }

  const lineCount = Math.max(content.split("\n").length, 1);

  return (
    <div className="flex h-[calc(100svh-9rem)] min-h-80 flex-col overflow-hidden rounded-lg border">
      <div className="flex items-center justify-between gap-3 border-b bg-muted/40 px-3 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <Button size="sm" variant="ghost" onClick={handleClose}>
            <ArrowLeft className="size-4" />
            Close
          </Button>
          <span className="truncate font-mono text-sm" title={path}>
            {baseName(path)}
            {dirty && <span className="ml-1 text-muted-foreground">•</span>}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {/* US-2.16: state the text-edit limit up front, not only on failure */}
          <span
            className="hidden text-xs text-muted-foreground md:block"
            title="Files up to 1 MB can be edited here; larger files are download-only."
          >
            edits up to 1 MB
          </span>
          {meta && (
            <span className="hidden text-xs text-muted-foreground sm:block">
              {meta.eol.toUpperCase()}
            </span>
          )}
          <Button size="sm" onClick={() => save(false)} disabled={!dirty || saving || !meta}>
            {saving ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}
            Save
          </Button>
        </div>
      </div>

      {error && (
        <p className="border-b bg-destructive/10 px-3 py-2 text-sm font-medium text-destructive">
          {error}
        </p>
      )}
      {justCreated && !error && (
        <p className="border-b bg-muted/40 px-3 py-1.5 text-xs text-muted-foreground">
          New empty file created. Add content and save.
        </p>
      )}

      {loading ? (
        <div className="flex flex-1 items-center justify-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" />
          Loading…
        </div>
      ) : notEditable ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 p-8 text-center text-sm text-muted-foreground">
          <p>{notEditable}</p>
          <p className="text-xs">Close this and use the Download action instead.</p>
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 overflow-hidden bg-background font-mono text-xs">
          <div
            ref={gutterRef}
            className="select-none overflow-hidden bg-muted/40 px-2 py-2 text-right text-muted-foreground"
            style={{ minWidth: "3rem" }}
          >
            {Array.from({ length: lineCount }, (_, i) => (
              <div key={i} className="leading-5">
                {i + 1}
              </div>
            ))}
          </div>
          <textarea
            ref={textareaRef}
            value={content}
            spellCheck={false}
            wrap="off"
            onScroll={(e) => {
              if (gutterRef.current) gutterRef.current.scrollTop = e.currentTarget.scrollTop;
            }}
            onChange={(e) => {
              setContent(e.target.value);
              setDirty(true);
            }}
            className="flex-1 resize-none overflow-auto whitespace-pre bg-transparent px-3 py-2 leading-5 outline-none"
          />
        </div>
      )}
    </div>
  );
}
