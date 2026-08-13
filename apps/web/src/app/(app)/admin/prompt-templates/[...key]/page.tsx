"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { ArrowLeft, Loader2, RotateCcw } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { MarkdownEditor } from "@/components/markdown-editor";
import { GROUP_META, type TemplateItem } from "../template-meta";

/** US-5.18: one template, one focused page. View lands on the editor's
 * Preview tab, Edit on Write; save/reset reuse the us-5.17 endpoints
 * unchanged. Deliberately NO orgId on the editor: platform templates are
 * served to every org, so org-scoped attachment:// image refs would be
 * unresolvable elsewhere — paste/drop/picker stay off. */

function formatWhen(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function TemplateDetail() {
  const params = useParams<{ key: string[] }>();
  const searchParams = useSearchParams();
  const key = (params.key ?? []).map(decodeURIComponent).join("/");
  const mode = searchParams.get("mode") === "edit" ? "write" : "preview";

  const [item, setItem] = useState<TemplateItem | null | undefined>(undefined);
  const [content, setContent] = useState("");
  const [busy, setBusy] = useState<"save" | "reset" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    try {
      const items: TemplateItem[] = await apiFetch(
        "/api/v1/admin/prompt-templates"
      );
      const found = items.find((i) => i.key === key) ?? null;
      setItem(found);
      if (found) setContent(found.override?.content ?? found.default);
    } catch (e) {
      setError((e as Error).message);
      setItem(null);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const effective = item ? (item.override?.content ?? item.default) : "";
  const dirty = item != null && content !== effective;

  async function save() {
    setError(null);
    setBusy("save");
    try {
      await apiFetch(
        `/api/v1/admin/prompt-templates/${encodeURIComponent(key)}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content }),
        }
      );
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function reset() {
    setError(null);
    setBusy("reset");
    try {
      await apiFetch(
        `/api/v1/admin/prompt-templates/${encodeURIComponent(key)}`,
        { method: "DELETE" }
      );
      await load();
      if (item) setContent(item.default);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="flex w-full flex-col gap-5">
      <Link
        href="/admin/prompt-templates"
        className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-4" />
        Prompt templates
      </Link>

      {item === undefined ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : item === null ? (
        <div className="rounded-md border p-6 text-sm text-muted-foreground">
          No template with the key <code className="rounded bg-muted px-1">{key}</code>.{" "}
          <Link href="/admin/prompt-templates" className="underline">
            Back to the list.
          </Link>
        </div>
      ) : (
        <>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0 space-y-1.5">
              <h1 className="flex flex-wrap items-center gap-2 text-2xl font-semibold tracking-tight">
                {item.label}
                <Badge variant="outline" className="font-normal">
                  {GROUP_META[item.group].badge}
                </Badge>
                {item.override ? (
                  <Badge className="font-normal">Customized</Badge>
                ) : (
                  <Badge variant="secondary" className="font-normal">
                    Factory default
                  </Badge>
                )}
              </h1>
              <p className="text-sm text-muted-foreground">
                {item.description}
              </p>
              {item.variables.length > 0 && (
                <p className="font-mono text-xs text-muted-foreground">
                  Allowed placeholders:{" "}
                  {item.variables.map((v) => `{${v}}`).join(" · ")}
                </p>
              )}
            </div>
            {item.override && (
              <Button
                variant="outline"
                size="sm"
                disabled={busy !== null}
                onClick={reset}
              >
                {busy === "reset" ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <RotateCcw className="size-3.5" />
                )}
                Reset to default
              </Button>
            )}
          </div>

          <MarkdownEditor
            rows={Math.min(24, Math.max(8, content.split("\n").length + 2))}
            value={content}
            onChange={setContent}
            defaultTab={mode}
          />

          <div className="flex items-center justify-between gap-3">
            <p className="text-xs text-muted-foreground">
              {item.override
                ? `Customized ${formatWhen(item.override.updated_at)}` +
                  (item.override.updated_by
                    ? ` by ${item.override.updated_by}`
                    : "")
                : "Serving the factory default."}
            </p>
            {dirty && (
              <Button size="sm" disabled={busy !== null} onClick={save}>
                {busy === "save" && <Loader2 className="size-4 animate-spin" />}
                Save
              </Button>
            )}
          </div>

          {error && (
            <p className="text-sm font-medium text-destructive">{error}</p>
          )}
        </>
      )}
    </div>
  );
}

export default function TemplateDetailPage() {
  return (
    <Suspense
      fallback={<p className="text-sm text-muted-foreground">Loading…</p>}
    >
      <TemplateDetail />
    </Suspense>
  );
}
