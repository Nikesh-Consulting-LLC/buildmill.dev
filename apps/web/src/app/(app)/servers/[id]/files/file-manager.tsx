"use client";

import { confirmDialog } from "@/components/ui/confirm-dialog";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ChevronRight,
  Download,
  File as FileIcon,
  FileArchive,
  FilePlus,
  Folder,
  FolderPlus,
  Link2,
  Loader2,
  Pencil,
  RefreshCw,
  Trash2,
  Upload,
} from "lucide-react";
import {
  API_URL,
  apiCall,
  ApiError,
  getAccessToken,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { FileEditor } from "./file-editor";
import { baseName, breadcrumbs, joinPath, parentPath } from "./path-utils";

type Entry = {
  name: string;
  type: "dir" | "file" | "link" | "other";
  size: number;
  mtime: number;
};

type UploadItem = { name: string; progress: number; error?: string };

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

function formatWhen(epoch: number): string {
  if (!epoch) return "";
  return new Date(epoch * 1000).toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function FileManager({ serverId }: { serverId: string }) {
  const [path, setPath] = useState<string>("");
  const [pathInput, setPathInput] = useState<string>("");
  const [entries, setEntries] = useState<Entry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [uploads, setUploads] = useState<UploadItem[]>([]);
  const [dragging, setDragging] = useState(false);
  const [editing, setEditing] = useState<{ path: string; created?: boolean } | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const load = useCallback(
    async (target: string) => {
      setLoading(true);
      setError(null);
      try {
        const data = await apiCall(
          `/api/v1/servers/${serverId}/files?path=${encodeURIComponent(target || "~")}`
        );
        setPath(data.path);
        setPathInput(data.path);
        setEntries(data.entries);
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setLoading(false);
      }
    },
    [serverId]
  );

  useEffect(() => {
    // Initial listing fetch on mount; load() flips its own loading flag.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load("~");
  }, [load]);

  async function uploadFiles(files: FileList | File[]) {
    const token = await getAccessToken();
    const list = Array.from(files);
    setUploads(list.map((f) => ({ name: f.name, progress: 0 })));

    // Sequential so progress is readable; a failure of one doesn't abort the rest.
    for (let i = 0; i < list.length; i++) {
      const file = list[i];
      await new Promise<void>((resolve) => {
        const xhr = new XMLHttpRequest();
        const url = `${API_URL}/api/v1/servers/${serverId}/files/upload?path=${encodeURIComponent(path)}`;
        xhr.open("POST", url);
        xhr.setRequestHeader("Authorization", `Bearer ${token}`);
        xhr.upload.onprogress = (ev) => {
          if (ev.lengthComputable) {
            const pct = Math.round((ev.loaded / ev.total) * 100);
            setUploads((u) => u.map((x, idx) => (idx === i ? { ...x, progress: pct } : x)));
          }
        };
        xhr.onload = () => {
          if (xhr.status >= 200 && xhr.status < 300) {
            setUploads((u) => u.map((x, idx) => (idx === i ? { ...x, progress: 100 } : x)));
          } else {
            let detail = `Upload failed (${xhr.status})`;
            try {
              detail = JSON.parse(xhr.responseText).detail ?? detail;
            } catch {
              /* keep default */
            }
            setUploads((u) => u.map((x, idx) => (idx === i ? { ...x, error: detail } : x)));
          }
          resolve();
        };
        xhr.onerror = () => {
          setUploads((u) => u.map((x, idx) => (idx === i ? { ...x, error: "Network error" } : x)));
          resolve();
        };
        const form = new FormData();
        form.append("file", file);
        xhr.send(form);
      });
    }
    await load(path);
    setTimeout(() => setUploads([]), 4000);
  }

  async function download(entry: Entry) {
    try {
      const token = await getAccessToken();
      const url = `${API_URL}/api/v1/servers/${serverId}/files/download?path=${encodeURIComponent(joinPath(path, entry.name))}`;
      const resp = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
      if (!resp.ok) throw new Error("Download failed");
      const blob = await resp.blob();
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = entry.name;
      link.click();
      URL.revokeObjectURL(link.href);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function extract(entry: Entry) {
    if (
      !(await confirmDialog({
        title: "Extract archive?",
        description: `Extract ${entry.name} here? Existing files with the same names are overwritten.`,
        confirmLabel: "Extract",
      }))
    )
      return;
    setError(null);
    try {
      await apiCall(`/api/v1/servers/${serverId}/files/extract`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: joinPath(path, entry.name) }),
      });
      await load(path);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function makeFolder() {
    const name = prompt("New folder name:");
    if (!name) return;
    setError(null);
    try {
      await apiCall(`/api/v1/servers/${serverId}/files/mkdir`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: joinPath(path, name) }),
      });
      await load(path);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function newFile() {
    const name = prompt("New file name:");
    if (!name) return;
    if (name.includes("/")) {
      setError("File name can't contain '/'.");
      return;
    }
    setError(null);
    try {
      const target = joinPath(path, name);
      await apiCall(`/api/v1/servers/${serverId}/files/new`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: target }),
      });
      setEditing({ path: target, created: true });
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function remove(entry: Entry) {
    const target = joinPath(path, entry.name);
    const isDir = entry.type === "dir";
    if (
      !(await confirmDialog({
        title: `Delete ${isDir ? "folder" : "file"}?`,
        description: `"${entry.name}" will be deleted.`,
        confirmLabel: "Delete",
        destructive: true,
      }))
    )
      return;
    setError(null);
    try {
      await apiCall(`/api/v1/servers/${serverId}/files/delete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: target, recursive: false }),
      });
      await load(path);
    } catch (e) {
      if (e instanceof ApiError && e.status === 400 && /isn't empty/i.test(e.message)) {
        if (
          await confirmDialog({
            title: "Folder isn't empty",
            description: `"${entry.name}" isn't empty. Delete it and everything inside?`,
            confirmLabel: "Delete all",
            destructive: true,
          })
        ) {
          try {
            await apiCall(`/api/v1/servers/${serverId}/files/delete`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ path: target, recursive: true }),
            });
            await load(path);
          } catch (e2) {
            setError((e2 as Error).message);
          }
        }
        return;
      }
      setError((e as Error).message);
    }
  }

  function onEntryOpen(entry: Entry) {
    if (entry.type === "dir") {
      load(joinPath(path, entry.name));
    } else if (entry.type === "file") {
      setEditing({ path: joinPath(path, entry.name) });
    }
  }

  if (editing) {
    return (
      <FileEditor
        serverId={serverId}
        path={editing.path}
        justCreated={editing.created}
        onClose={() => {
          setEditing(null);
          load(path);
        }}
      />
    );
  }

  const crumbs = path ? breadcrumbs(path) : [];

  return (
    <div
      className="flex flex-col gap-3 rounded-lg border"
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        if (e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files);
      }}
    >
      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2 border-b p-2">
        <Button size="sm" variant="outline" onClick={() => load(parentPath(path))} disabled={!path || path === "/"}>
          Up
        </Button>
        <Button size="sm" variant="outline" onClick={() => load(path)}>
          <RefreshCw className="size-4" />
        </Button>
        <form
          className="flex min-w-0 flex-1 items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            load(pathInput);
          }}
        >
          <Input
            value={pathInput}
            onChange={(e) => setPathInput(e.target.value)}
            placeholder="/absolute/path"
            className="h-8 min-w-40 flex-1 font-mono text-xs"
          />
        </form>
        <Button size="sm" variant="outline" onClick={() => fileInputRef.current?.click()}>
          <Upload className="size-4" />
          Upload
        </Button>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={(e) => {
            if (e.target.files?.length) uploadFiles(e.target.files);
            e.target.value = "";
          }}
        />
        <Button size="sm" variant="outline" onClick={makeFolder}>
          <FolderPlus className="size-4" />
          Folder
        </Button>
        <Button size="sm" variant="outline" onClick={newFile}>
          <FilePlus className="size-4" />
          New file
        </Button>
      </div>

      {/* Breadcrumb */}
      <div className="flex flex-wrap items-center gap-0.5 px-3 text-xs text-muted-foreground">
        {crumbs.map((c, i) => (
          <span key={c.path} className="flex items-center gap-0.5">
            {i > 0 && <ChevronRight className="size-3" />}
            <button className="rounded px-1 py-0.5 hover:bg-muted hover:text-foreground" onClick={() => load(c.path)}>
              {c.label}
            </button>
          </span>
        ))}
      </div>

      {/* Uploads */}
      {uploads.length > 0 && (
        <div className="mx-3 flex flex-col gap-1 rounded-md border bg-muted/40 p-2 text-xs">
          {uploads.map((u) => (
            <div key={u.name} className="flex items-center justify-between gap-2">
              <span className="truncate font-mono">{u.name}</span>
              {u.error ? (
                <span className="text-destructive">{u.error}</span>
              ) : (
                <span className="text-muted-foreground">{u.progress}%</span>
              )}
            </div>
          ))}
        </div>
      )}

      {error && <p className="px-3 text-sm font-medium text-destructive">{error}</p>}

      {/* Listing */}
      <div className={dragging ? "relative" : ""}>
        {dragging && (
          <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center rounded-md border-2 border-dashed border-ring bg-background/80 text-sm font-medium">
            Drop files to upload here
          </div>
        )}
        {loading ? (
          <div className="flex items-center justify-center gap-2 p-8 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" />
            Loading…
          </div>
        ) : entries.length === 0 ? (
          <p className="p-8 text-center text-sm text-muted-foreground">This folder is empty.</p>
        ) : (
          <ul className="divide-y">
            {entries.map((entry) => {
              const isZip = entry.type === "file" && entry.name.toLowerCase().endsWith(".zip");
              const Icon =
                entry.type === "dir" ? Folder : entry.type === "link" ? Link2 : isZip ? FileArchive : FileIcon;
              const openable = entry.type === "dir" || entry.type === "file";
              return (
                <li key={entry.name} className="flex items-center gap-3 px-3 py-2 text-sm hover:bg-muted/40">
                  <button
                    className="flex min-w-0 flex-1 items-center gap-2 text-left"
                    onClick={() => onEntryOpen(entry)}
                    disabled={!openable}
                  >
                    <Icon className={entry.type === "dir" ? "size-4 text-sky-500" : "size-4 text-muted-foreground"} />
                    <span className="truncate">{entry.name}</span>
                  </button>
                  <span className="hidden w-24 shrink-0 text-right text-xs text-muted-foreground sm:block">
                    {entry.type === "file" ? formatSize(entry.size) : ""}
                  </span>
                  <span className="hidden w-40 shrink-0 text-right text-xs text-muted-foreground md:block">
                    {formatWhen(entry.mtime)}
                  </span>
                  <span className="flex shrink-0 items-center gap-1">
                    {entry.type === "file" && (
                      <>
                        <Button size="icon-sm" variant="ghost" title="Edit" onClick={() => setEditing({ path: joinPath(path, entry.name) })}>
                          <Pencil className="size-3.5" />
                        </Button>
                        <Button size="icon-sm" variant="ghost" title="Download" onClick={() => download(entry)}>
                          <Download className="size-3.5" />
                        </Button>
                        {isZip && (
                          <Button size="icon-sm" variant="ghost" title="Extract" onClick={() => extract(entry)}>
                            <FileArchive className="size-3.5" />
                          </Button>
                        )}
                      </>
                    )}
                    <Button size="icon-sm" variant="ghost" title="Delete" onClick={() => remove(entry)}>
                      <Trash2 className="size-3.5" />
                    </Button>
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </div>
      <div className="px-3 pb-2 text-xs text-muted-foreground">{baseName(path) || "/"}</div>
    </div>
  );
}
