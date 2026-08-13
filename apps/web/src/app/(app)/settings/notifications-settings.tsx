"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { CheckCircle2, Loader2, Send, Trash2, Webhook, XCircle } from "lucide-react";
import { apiCall } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { EmptyState } from "@/components/empty-state";
import { ConfirmDialog } from "@/components/confirm-dialog";

export type EndpointRow = {
  id: string;
  name: string;
  url_host: string;
  format: string;
  last_delivery_at: string | null;
  last_delivery_ok: boolean | null;
  last_delivery_error: string | null;
};

const FORMATS = [
  { value: "json", label: "JSON" },
  { value: "slack", label: "Slack (incoming webhook)" },
];

/** US-1.44: org-level notification endpoints. URLs are secrets — stored
 * write-only; the row shows name + "URL set · <host>" only. */
export function NotificationsSettings({
  orgId,
  endpoints,
}: {
  orgId: string;
  endpoints: EndpointRow[];
}) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [format, setFormat] = useState("slack");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<Record<string, string>>({});

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await apiCall("/api/v1/notifications/endpoints", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ org_id: orgId, name, url, format }),
      });
      setName("");
      setUrl("");
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function handleTest(id: string) {
    setTestResult((prev) => ({ ...prev, [id]: "…" }));
    try {
      const resp = (await apiCall(`/api/v1/notifications/endpoints/${id}/test`, {
        method: "POST",
      })) as { ok: boolean; error: string | null };
      setTestResult((prev) => ({
        ...prev,
        [id]: resp.ok ? "Delivered ✓" : `Failed: ${resp.error ?? "unknown"}`,
      }));
      router.refresh();
    } catch (e) {
      setTestResult((prev) => ({ ...prev, [id]: `Failed: ${(e as Error).message}` }));
    }
  }

  async function handleDelete(id: string) {
    await apiCall(`/api/v1/notifications/endpoints/${id}`, { method: "DELETE" });
    router.refresh();
  }

  return (
    <div className="flex flex-col gap-4">
      {endpoints.length === 0 ? (
        <EmptyState
          icon={Webhook}
          title="No endpoints"
          description="Add a webhook (e.g. a Slack incoming webhook) to hear about deploy failures and rollbacks without watching logs."
        />
      ) : (
        <ul className="grid gap-1.5">
          {endpoints.map((ep) => (
            <li
              key={ep.id}
              className="flex items-center justify-between gap-3 rounded-md border px-3 py-2 text-sm"
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="truncate font-medium">{ep.name}</span>
                  <span className="text-xs text-muted-foreground">
                    URL set · {ep.url_host} · {ep.format}
                  </span>
                </div>
                <p className="mt-0.5 flex items-center gap-1.5 text-xs text-muted-foreground">
                  {ep.last_delivery_at ? (
                    <>
                      {ep.last_delivery_ok ? (
                        <CheckCircle2 className="size-3 text-emerald-600" />
                      ) : (
                        <XCircle className="size-3 text-destructive" />
                      )}
                      Last delivery{" "}
                      {new Date(ep.last_delivery_at).toLocaleString(undefined, {
                        month: "short",
                        day: "numeric",
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
                      {!ep.last_delivery_ok && ep.last_delivery_error && (
                        <span className="text-destructive">
                          — {ep.last_delivery_error}
                        </span>
                      )}
                    </>
                  ) : (
                    "No deliveries yet"
                  )}
                  {testResult[ep.id] && <span>· {testResult[ep.id]}</span>}
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-1">
                <Button variant="ghost" size="sm" onClick={() => handleTest(ep.id)}>
                  <Send className="size-3.5" />
                  Test
                </Button>
                <ConfirmDialog
                  trigger={
                    <Button variant="ghost" size="sm">
                      <Trash2 className="size-3.5" />
                    </Button>
                  }
                  title={`Delete endpoint "${ep.name}"?`}
                  description="Deployments will stop notifying this webhook."
                  confirmLabel="Delete endpoint"
                  onConfirm={() => handleDelete(ep.id)}
                />
              </div>
            </li>
          ))}
        </ul>
      )}

      <form onSubmit={handleAdd} className="grid gap-3">
        <div className="grid grid-cols-[1fr_1fr] gap-2">
          <div className="grid gap-2">
            <Label htmlFor="ep-name">Name</Label>
            <Input
              id="ep-name"
              placeholder="team-slack"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="ep-format">Format</Label>
            <Select
              items={FORMATS}
              value={format}
              onValueChange={(v) => {
                if (typeof v === "string") setFormat(v);
              }}
            >
              <SelectTrigger id="ep-format" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {FORMATS.map((f) => (
                  <SelectItem key={f.value} value={f.value}>
                    {f.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        <div className="grid gap-2">
          <Label htmlFor="ep-url">Webhook URL</Label>
          <Input
            id="ep-url"
            type="password"
            autoComplete="new-password"
            placeholder="https://hooks.slack.com/services/…"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
          <p className="text-xs text-muted-foreground">
            Treated as a secret (Slack URLs embed tokens) — stored write-only,
            shown afterwards as its host only.
          </p>
        </div>
        {error && <p className="text-sm font-medium text-destructive">{error}</p>}
        <div>
          <Button type="submit" disabled={busy || !name.trim() || !url.trim()}>
            {busy && <Loader2 className="size-4 animate-spin" />}
            Add endpoint
          </Button>
        </div>
      </form>
    </div>
  );
}
