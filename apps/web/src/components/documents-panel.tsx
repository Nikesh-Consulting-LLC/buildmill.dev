"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  Download,
  FileText,
  FolderOpen,
  Loader2,
  RefreshCw,
  Trash2,
  Upload,
} from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import {
  deleteDocument,
  documentUrl,
  formatBytes,
  isPreviewableHtml,
  isPreviewableImage,
  matchesTarget,
  replaceDocumentFile,
  uploadDocument,
  type DocumentRow,
  type DocumentTarget,
} from "@/lib/documents";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { EmptyState } from "@/components/empty-state";

const SOURCE_LABEL: Record<DocumentRow["source"], string> = {
  user: "User",
  factory: "Factory",
  agent: "Agent",
};

export type DocumentLinkMeta = { label: string; href: string | null };

function formatWhen(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Inline preview for PRD documents (US-2.22), rendered safely: images
 * and SVG through <img> (scripts never execute in image context), HTML
 * in a sandboxed iframe. Everything else is download-only. */
function DocumentPreview({ doc }: { doc: DocumentRow }) {
  const [url, setUrl] = useState<string | null>(null);
  const image = isPreviewableImage(doc.mime_type);
  const html = isPreviewableHtml(doc.mime_type);

  useEffect(() => {
    if (!image && !html) return;
    let cancelled = false;
    documentUrl(doc)
      .then((u) => {
        if (!cancelled) setUrl(u);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
    // Re-sign when the file is replaced.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doc.id, doc.updated_at, doc.storage_path]);

  if ((!image && !html) || !url) return null;
  if (image) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={url}
        alt={doc.name}
        className="max-h-96 w-full rounded-md border bg-white object-contain"
      />
    );
  }
  return (
    <iframe
      src={url}
      sandbox=""
      title={doc.name}
      className="h-96 w-full rounded-md border bg-white"
    />
  );
}

export function DocumentsPanel({
  orgId,
  projectId,
  target,
  initialDocs,
  actorNames = {},
  preview = false,
  linkMeta,
  variant = "card",
  title = "Documents",
  description = "Files attached to this work item — specs, assets, notes. Available to the AI worker on dispatch.",
  emptyTitle = "No documents yet",
  emptyDescription = "Upload a file, or let an agent attach one mid-run.",
}: {
  orgId: string;
  projectId: string;
  /** Where uploads land. The project target also *lists* every document
   * in the project (the project Documents tab). */
  target: DocumentTarget;
  initialDocs: DocumentRow[];
  actorNames?: Record<string, string>;
  /** US-2.22: inline preview (PRD section). */
  preview?: boolean;
  /** US-2.22: link column for the project tab, keyed by document id. */
  linkMeta?: Record<string, DocumentLinkMeta>;
  variant?: "card" | "plain";
  title?: string;
  description?: string;
  emptyTitle?: string;
  emptyDescription?: string;
}) {
  const [docs, setDocs] = useState<DocumentRow[]>(initialDocs);
  const [busy, setBusy] = useState<string | null>(null); // "upload" | doc id
  const [error, setError] = useState<string | null>(null);
  const uploadRef = useRef<HTMLInputElement>(null);
  const replaceRef = useRef<HTMLInputElement>(null);
  const replaceTarget = useRef<DocumentRow | null>(null);

  const listAll = target.attachedTo === "project";
  const inScope = (doc: DocumentRow) =>
    listAll ? true : matchesTarget(doc, target);

  // Sync when the server sends a new result set (router.refresh()).
  const [prevInitial, setPrevInitial] = useState(initialDocs);
  if (prevInitial !== initialDocs) {
    setPrevInitial(initialDocs);
    setDocs(initialDocs);
  }

  // Agent/factory documents appear without a manual refresh (US-2.21).
  useEffect(() => {
    const supabase = createClient();
    let channel: ReturnType<typeof supabase.channel> | null = null;
    let cancelled = false;

    async function subscribe() {
      const {
        data: { session },
      } = await supabase.auth.getSession();
      if (!session || cancelled) return;
      await supabase.realtime.setAuth(session.access_token);

      const suffix =
        "issueId" in target
          ? `${target.attachedTo}-${target.issueId}`
          : "testCaseId" in target
            ? `test-case-${target.testCaseId}`
            : "project";
      channel = supabase
        .channel(`documents-${projectId}-${suffix}`, { config: { private: false } })
        .on(
          "postgres_changes",
          {
            event: "*",
            schema: "public",
            table: "documents",
            filter: `project_id=eq.${projectId}`,
          },
          (payload) => {
            if (payload.eventType === "DELETE") {
              const old = payload.old as Partial<DocumentRow>;
              setDocs((prev) => prev.filter((d) => d.id !== old.id));
              return;
            }
            const row = payload.new as DocumentRow;
            setDocs((prev) => {
              const rest = prev.filter((d) => d.id !== row.id);
              if (!inScope(row)) return rest;
              return [...rest, row].sort((a, b) =>
                a.created_at.localeCompare(b.created_at)
              );
            });
          }
        )
        .subscribe();
    }

    subscribe();
    return () => {
      cancelled = true;
      if (channel) supabase.removeChannel(channel);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  async function handleUpload(files: FileList | null) {
    if (!files?.length) return;
    setError(null);
    setBusy("upload");
    try {
      for (const file of Array.from(files)) {
        const doc = await uploadDocument(orgId, projectId, target, file);
        setDocs((prev) =>
          prev.some((d) => d.id === doc.id) ? prev : [...prev, doc]
        );
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
      if (uploadRef.current) uploadRef.current.value = "";
    }
  }

  async function handleReplace(files: FileList | null) {
    const doc = replaceTarget.current;
    if (!files?.length || !doc) return;
    setError(null);
    setBusy(doc.id);
    try {
      const updated = await replaceDocumentFile(doc, files[0]);
      setDocs((prev) => prev.map((d) => (d.id === updated.id ? updated : d)));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
      replaceTarget.current = null;
      if (replaceRef.current) replaceRef.current.value = "";
    }
  }

  async function handleDownload(doc: DocumentRow) {
    setError(null);
    try {
      const url = await documentUrl(doc, { download: true });
      window.open(url, "_blank");
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function handleDelete(doc: DocumentRow) {
    await deleteDocument(doc);
    setDocs((prev) => prev.filter((d) => d.id !== doc.id));
  }

  const list = (
    <div className="flex flex-col gap-4">
      {docs.length === 0 ? (
        <EmptyState
          icon={FolderOpen}
          title={emptyTitle}
          description={emptyDescription}
          className="p-6"
        />
      ) : (
        <ul className="grid gap-2">
          {docs.map((doc) => (
            <li key={doc.id} className="grid gap-2 rounded-md border px-3 py-2">
              <div className="flex items-center justify-between gap-3 text-sm">
                <span className="flex min-w-0 items-center gap-2">
                  <FileText className="size-4 shrink-0 text-muted-foreground" />
                  <button
                    type="button"
                    onClick={() => handleDownload(doc)}
                    className="truncate font-medium underline-offset-4 hover:underline"
                    title={doc.name}
                  >
                    {doc.name}
                  </button>
                  <Badge variant="secondary">{SOURCE_LABEL[doc.source]}</Badge>
                  {linkMeta?.[doc.id] &&
                    (linkMeta[doc.id].href ? (
                      <Link
                        href={linkMeta[doc.id].href!}
                        className="shrink-0 truncate text-xs text-muted-foreground hover:text-foreground hover:underline"
                      >
                        {linkMeta[doc.id].label}
                      </Link>
                    ) : (
                      <span className="shrink-0 text-xs text-muted-foreground">
                        {linkMeta[doc.id].label}
                      </span>
                    ))}
                </span>
                <span className="flex shrink-0 items-center gap-1">
                  <span className="mr-1 hidden text-xs text-muted-foreground sm:inline">
                    {formatBytes(doc.size_bytes)}
                    {doc.created_by && actorNames[doc.created_by]
                      ? ` · ${actorNames[doc.created_by]}`
                      : ""}
                    {` · ${formatWhen(doc.updated_at)}`}
                  </span>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    onClick={() => handleDownload(doc)}
                    title="Download"
                  >
                    <Download className="size-3.5" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    disabled={busy === doc.id}
                    onClick={() => {
                      replaceTarget.current = doc;
                      replaceRef.current?.click();
                    }}
                    title="Replace file"
                  >
                    {busy === doc.id ? (
                      <Loader2 className="size-3.5 animate-spin" />
                    ) : (
                      <RefreshCw className="size-3.5" />
                    )}
                  </Button>
                  <ConfirmDialog
                    trigger={
                      <Button variant="ghost" size="icon-sm" title="Delete">
                        <Trash2 className="size-3.5" />
                      </Button>
                    }
                    title={`Delete ${doc.name}?`}
                    description="Removes the document and its file. This cannot be undone."
                    confirmLabel="Delete document"
                    onConfirm={() => handleDelete(doc)}
                  />
                </span>
              </div>
              {preview && <DocumentPreview doc={doc} />}
            </li>
          ))}
        </ul>
      )}

      {error && <p className="text-sm font-medium text-destructive">{error}</p>}

      <input
        ref={uploadRef}
        type="file"
        multiple
        className="hidden"
        onChange={(e) => handleUpload(e.target.files)}
      />
      <input
        ref={replaceRef}
        type="file"
        className="hidden"
        onChange={(e) => handleReplace(e.target.files)}
      />
    </div>
  );

  const uploadButton = (
    <Button
      variant="outline"
      size="sm"
      disabled={busy === "upload"}
      onClick={() => uploadRef.current?.click()}
    >
      {busy === "upload" ? (
        <Loader2 className="size-4 animate-spin" />
      ) : (
        <Upload className="size-4" />
      )}
      Upload
    </Button>
  );

  if (variant === "plain") {
    return (
      <div className="grid gap-3">
        <div className="flex items-center justify-between gap-2">
          <p className="text-sm font-medium">{title}</p>
          {uploadButton}
        </div>
        {list}
      </div>
    );
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <div className="space-y-1.5">
          <CardTitle className="flex items-center gap-2 text-base">
            <FolderOpen className="size-4 text-muted-foreground" />
            {title}
          </CardTitle>
          <CardDescription>
            {description} Up to 25 MB per file.
          </CardDescription>
        </div>
        {uploadButton}
      </CardHeader>
      <CardContent>{list}</CardContent>
    </Card>
  );
}
