"use client";

// Manager-triggered test: can this runner actually reach the factory
// remote and get source onto disk? Same round-trip a real claimed run
// depends on (gitwork.prepare_checkout on the runner side), just asked
// for on demand instead of waiting for a run to find out.

import { useEffect, useState } from "react";
import { CheckCircle2, Loader2, XCircle } from "lucide-react";
import { apiCall, ApiError } from "@/lib/api";
import { createClient } from "@/lib/supabase/client";
import { cn } from "@/lib/utils";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

type ProjectOption = { id: string; name: string };

type Result =
  | { ok: true; base_sha: string; bytes: number; project: string }
  | { ok: false; error: string };

export function PrepareCodebaseButton({ workerId }: { workerId: string }) {
  const [projects, setProjects] = useState<ProjectOption[] | null>(null);
  const [projectId, setProjectId] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Result | null>(null);

  useEffect(() => {
    let cancelled = false;
    const supabase = createClient();
    supabase
      .from("worker_capabilities")
      .select("project_id, projects(id, name)")
      .eq("worker_id", workerId)
      .then(({ data }) => {
        if (cancelled) return;
        const opts = (data ?? [])
          .map((r) => r.projects as unknown as ProjectOption | null)
          .filter((p): p is ProjectOption => !!p);
        setProjects(opts);
        if (opts.length === 1) setProjectId(opts[0].id);
      });
    return () => {
      cancelled = true;
    };
  }, [workerId]);

  async function prepare() {
    if (!projectId) return;
    setBusy(true);
    setResult(null);
    try {
      const res = await apiCall(`/api/v1/runner/${workerId}/prepare-workspace`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project_id: projectId }),
      });
      setResult(
        res.ok
          ? { ok: true, base_sha: res.base_sha, bytes: res.bytes, project: res.project }
          : { ok: false, error: res.error || "unknown failure" }
      );
    } catch (e) {
      setResult({
        ok: false,
        error: e instanceof ApiError ? String(e.message) : "request failed",
      });
    } finally {
      setBusy(false);
    }
  }

  if (projects !== null && projects.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        No project access yet — grant it above to test codebase checkout.
      </p>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      {projects && projects.length > 1 && (
        <Select
          items={projects.map((p) => ({ value: p.id, label: p.name }))}
          value={projectId}
          onValueChange={(v) => typeof v === "string" && setProjectId(v)}
        >
          <SelectTrigger size="sm" className="w-40">
            <SelectValue placeholder="Project…" />
          </SelectTrigger>
          <SelectContent>
            {projects.map((p) => (
              <SelectItem key={p.id} value={p.id}>
                {p.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
      <button
        type="button"
        disabled={busy || !projectId}
        onClick={() => void prepare()}
        className="rounded-md border px-2 py-1 text-xs hover:bg-muted disabled:opacity-50"
      >
        {busy ? (
          <span className="flex items-center gap-1">
            <Loader2 className="size-3 animate-spin" /> Preparing…
          </span>
        ) : (
          "Prepare codebase"
        )}
      </button>
      {result && (
        <span
          className={cn(
            "flex items-center gap-1 text-xs",
            result.ok
              ? "text-emerald-600 dark:text-emerald-400"
              : "text-red-600 dark:text-red-400"
          )}
        >
          {result.ok ? (
            <>
              <CheckCircle2 className="size-3.5" />
              ready — {result.project} @ {result.base_sha.slice(0, 7)} (
              {Math.round(result.bytes / 1024)} KB on disk)
            </>
          ) : (
            <>
              <XCircle className="size-3.5" />
              {result.error}
            </>
          )}
        </span>
      )}
    </div>
  );
}
