"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { AlertTriangle, GitCommitHorizontal, Loader2, Tag } from "lucide-react";
import { apiFetch } from "@/lib/api";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

type PreviewItem = {
  issue_id: string;
  title: string;
  type: string;
  display_id: string | null;
};

type Preview = {
  version: string | null;
  commit_sha: string | null;
  branch: string | null;
  previous: { version: string; commit_sha: string } | null;
  items: PreviewItem[];
  commit_count?: number;
  first_release: boolean;
  truncated: boolean;
  blockers: string[];
};

/** US-21.1: cut a release from the default branch.
 *
 * The preview is loaded when the dialog opens, and the commit it shows is the
 * one that gets pinned — everything downstream reads that SHA, never "the
 * branch head now". `blockers` are surfaced up front so the manager sees what
 * to fix instead of finding out on submit. */
export function CutReleaseDialog({
  projects,
  defaultProjectId,
  trigger,
}: {
  /** US-23.2: the hub spans projects, so the dialog asks which one first.
   * The preview costs GitHub calls (branch head, commit range), so it loads
   * only once a project is chosen rather than eagerly for every project. */
  projects: { id: string; name: string }[];
  /** US-91.18: opened from a card that already knows the project. */
  defaultProjectId?: string;
  /** US-91.18: the dashboard's card supplies its own button, so the two
   *  entry points share this dialog rather than growing a second one. */
  trigger?: React.ReactNode;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [projectId, setProjectId] = useState(
    defaultProjectId ?? projects[0]?.id ?? ""
  );
  const [preview, setPreview] = useState<Preview | null>(null);
  const [loading, setLoading] = useState(false);
  const [version, setVersion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load(pid = projectId) {
    if (!pid) return;
    setLoading(true);
    setError(null);
    setPreview(null);
    try {
      const p = (await apiFetch(
        `/api/v1/projects/${pid}/releases/preview`
      )) as Preview;
      setPreview(p);
      setVersion(p.version ?? "");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  async function cut() {
    setBusy(true);
    setError(null);
    try {
      const created = (await apiFetch(`/api/v1/projects/${projectId}/releases`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ version: version.trim() || null }),
      })) as { id: string; tag_error?: string | null };
      if (created.tag_error) {
        // The release exists; only the git tag failed. Say so rather than
        // closing on a silent half-success.
        setError(`Release cut, but tagging failed: ${created.tag_error}`);
        router.refresh();
        return;
      }
      setOpen(false);
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const blocked = !!preview?.blockers.length;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) load();
      }}
    >
      {trigger ? (
        <DialogTrigger render={<button type="button" />}>{trigger}</DialogTrigger>
      ) : (
        <DialogTrigger render={<Button variant="create" size="sm" />}>
          <Tag className="size-4" />
          Cut release
        </DialogTrigger>
      )}
      <DialogContent className="flex max-h-[85vh] flex-col overflow-hidden sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Cut a release</DialogTitle>
          <DialogDescription>
            Pins the head of {preview?.branch ?? "the default branch"} and ships
            it to UAT. Production is reached only by promotion, after sign-off.
          </DialogDescription>
        </DialogHeader>

        <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto pr-1">
          {projects.length > 1 && (
            <div className="grid gap-2">
              <Label htmlFor="release-project">Project</Label>
              <Select
                items={projects.map((p) => ({ value: p.id, label: p.name }))}
                value={projectId}
                onValueChange={(v) => {
                  if (typeof v !== "string") return;
                  setProjectId(v);
                  // The preview belongs to one project — reload it rather
                  // than leave another project's commit on screen.
                  load(v);
                }}
              >
                <SelectTrigger id="release-project" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {projects.map((p) => (
                    <SelectItem key={p.id} value={p.id}>
                      {p.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}

          {loading && (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" />
              Working out what would be in it…
            </p>
          )}

          {preview?.blockers.map((b) => (
            <p
              key={b}
              className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm font-medium text-destructive"
            >
              <AlertTriangle className="mt-0.5 size-4 shrink-0" />
              {b}
            </p>
          ))}

          {preview && !blocked && (
            <>
              <div className="grid gap-2">
                <Label htmlFor="release-version">Version</Label>
                <Input
                  id="release-version"
                  value={version}
                  onChange={(e) => setVersion(e.target.value)}
                  className="font-mono"
                />
                <p className="text-xs text-muted-foreground">
                  Proposed from today&apos;s date. Override it if you want —
                  the version is fixed here, and the agent only ever reads it.
                </p>
              </div>

              <div className="rounded-md border px-3 py-2 text-xs text-muted-foreground">
                <span className="inline-flex items-center gap-1.5">
                  <GitCommitHorizontal className="size-3.5" />
                  Pinning{" "}
                  <span className="font-mono text-foreground">
                    {preview.commit_sha?.slice(0, 7)}
                  </span>
                </span>
                {preview.previous ? (
                  <> · {preview.commit_count} commits since {preview.previous.version}</>
                ) : (
                  <> · first release{preview.truncated ? " (history capped)" : ""}</>
                )}
              </div>

              <div className="grid gap-2">
                <Label>
                  Included work items
                  <span className="ml-1 font-normal text-muted-foreground">
                    ({preview.items.length})
                  </span>
                </Label>
                {preview.items.length === 0 ? (
                  <p className="text-sm text-muted-foreground">
                    No work item in this range has a recorded merge commit. The
                    release still ships the pinned commit.
                  </p>
                ) : (
                  <ul className="grid gap-1">
                    {preview.items.map((i) => (
                      <li
                        key={i.issue_id}
                        className="flex items-baseline gap-2 rounded-md border px-2 py-1 text-sm"
                      >
                        {i.display_id && (
                          <span className="shrink-0 font-mono text-xs text-muted-foreground">
                            {i.display_id}
                          </span>
                        )}
                        <span className="min-w-0 truncate">{i.title}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </>
          )}

          {error && (
            <p className="text-sm font-medium text-destructive">{error}</p>
          )}
        </div>

        <DialogFooter>
          <Button
            disabled={busy || loading || blocked || !preview}
            onClick={cut}
          >
            {busy && <Loader2 className="size-4 animate-spin" />}
            Cut {version || "release"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
