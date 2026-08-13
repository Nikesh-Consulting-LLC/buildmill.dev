"use client";

import { useRef, useState } from "react";
import { FileArchive, Loader2, RotateCcw, Upload } from "lucide-react";
import { API_URL, apiCall, getAccessToken } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";

const MAX_MB = 200;

export type StagedZip = {
  filename: string;
  uploadedAt: string;
  bytes: number;
} | null;

/** US-1.33: ship a pre-built artifact through the same pipeline. */
export function ZipDeployDialog({
  deploymentId,
  deploymentName,
  serverLabel,
  targetFolder,
  stagedZip,
  isProtected,
  disabled,
  onStarted,
}: {
  deploymentId: string;
  deploymentName: string;
  serverLabel: string;
  targetFolder: string;
  stagedZip: StagedZip;
  isProtected?: boolean;
  disabled?: boolean;
  onStarted: (runId: string) => void;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [typed, setTyped] = useState("");
  const typeBlocked = !!isProtected && typed !== deploymentName;

  function uploadWithProgress(f: File): Promise<{ run_id: string }> {
    // XHR instead of fetch: upload progress events (US-1.33).
    return new Promise((resolve, reject) => {
      (async () => {
        const token = await getAccessToken();
        const xhr = new XMLHttpRequest();
        xhr.open("POST", `${API_URL}/api/v1/deployments/${deploymentId}/zip`);
        xhr.setRequestHeader("Authorization", `Bearer ${token}`);
        xhr.upload.onprogress = (e) => {
          if (e.lengthComputable) setProgress(Math.round((e.loaded / e.total) * 100));
        };
        xhr.onload = () => {
          try {
            const body = JSON.parse(xhr.responseText || "{}");
            if (xhr.status >= 200 && xhr.status < 300) resolve(body);
            else reject(new Error(body.detail ?? `Upload failed (${xhr.status})`));
          } catch {
            reject(new Error(`Upload failed (${xhr.status})`));
          }
        };
        xhr.onerror = () => reject(new Error("Upload failed — network error"));
        const form = new FormData();
        form.append("file", f);
        xhr.send(form);
      })().catch(reject);
    });
  }

  async function handleDeploy() {
    if (!file) return;
    setError(null);
    if (!file.name.toLowerCase().endsWith(".zip")) {
      setError("Only .zip files are accepted.");
      return;
    }
    if (file.size > MAX_MB * 1024 * 1024) {
      setError(`This zip is larger than the ${MAX_MB} MB limit.`);
      return;
    }
    setBusy(true);
    setProgress(0);
    try {
      const resp = await uploadWithProgress(file);
      setOpen(false);
      setFile(null);
      onStarted(resp.run_id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      setProgress(null);
    }
  }

  async function handleRedeploy() {
    setError(null);
    setBusy(true);
    try {
      const resp = (await apiCall(
        `/api/v1/deployments/${deploymentId}/redeploy-zip`,
        { method: "POST" }
      )) as { run_id: string };
      setOpen(false);
      onStarted(resp.run_id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button variant="outline" size="sm" disabled={disabled} />}>
        <FileArchive className="size-3.5" />
        Deploy from zip
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Deploy from zip</DialogTitle>
          <DialogDescription>
            A prepared artifact ships as-is through the same pipeline — same
            server (<span className="font-mono">{serverLabel}</span>), target
            folder (<span className="font-mono">{targetFolder}</span>), script,
            logs, and history. Source folder/exclude settings don&apos;t apply
            to zips. Max {MAX_MB} MB.
          </DialogDescription>
        </DialogHeader>

        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => fileRef.current?.click()}
            disabled={busy}
          >
            <Upload className="size-3.5" />
            Choose .zip
          </Button>
          <input
            ref={fileRef}
            type="file"
            accept=".zip"
            className="hidden"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
          {file && (
            <span className="truncate text-sm text-muted-foreground">
              {file.name} ({(file.size / 1_048_576).toFixed(1)} MB)
            </span>
          )}
        </div>

        {progress !== null && (
          <div className="h-2 w-full overflow-hidden rounded bg-muted">
            <div
              className="h-full bg-primary transition-all"
              style={{ width: `${progress}%` }}
            />
          </div>
        )}

        {stagedZip && (
          <div className="flex items-center justify-between gap-2 rounded-md border px-3 py-2 text-sm">
            <span className="min-w-0 truncate text-muted-foreground">
              Last staged: <span className="font-mono">{stagedZip.filename}</span>{" "}
              · {(stagedZip.bytes / 1_048_576).toFixed(1)} MB ·{" "}
              {new Date(stagedZip.uploadedAt).toLocaleString(undefined, {
                month: "short",
                day: "numeric",
                hour: "2-digit",
                minute: "2-digit",
              })}
            </span>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={handleRedeploy}
              disabled={busy || typeBlocked}
              title={typeBlocked ? "Type the deployment name above first" : undefined}
            >
              <RotateCcw className="size-3.5" />
              Redeploy
            </Button>
          </div>
        )}

        {isProtected && (
          <div className="grid gap-1.5 rounded-md border border-destructive/40 bg-destructive/5 p-3">
            <p className="text-sm font-medium text-destructive">
              Protected deployment — type{" "}
              <span className="font-mono">{deploymentName}</span> to confirm:
            </p>
            <input
              className="h-8 rounded-md border bg-background px-2 font-mono text-sm"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              placeholder={deploymentName}
              autoComplete="off"
              spellCheck={false}
            />
          </div>
        )}
        {error && <p className="text-sm font-medium text-destructive">{error}</p>}
        <DialogFooter>
          <Button
            variant={isProtected ? "destructive" : "default"}
            onClick={handleDeploy}
            disabled={busy || !file || typeBlocked}
          >
            {busy && <Loader2 className="size-4 animate-spin" />}
            Upload &amp; deploy
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
