"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { KeyRound, Loader2, Trash2 } from "lucide-react";
import { apiCall } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { EmptyState } from "@/components/empty-state";
import { ConfirmDialog } from "@/components/confirm-dialog";

const NAME_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;

/** US-1.37: names visible, values write-only — shown as "•••• set" with
 * replace and remove as the only operations. No reveal, no download. */
export function EnvVarsPanel({
  deploymentId,
  names,
}: {
  deploymentId: string;
  names: string[];
}) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isReplace = names.includes(name.trim());

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    const n = name.trim();
    if (!NAME_RE.test(n)) {
      setError(
        "Names must be valid POSIX env var names (letters, digits, underscore; not starting with a digit)."
      );
      return;
    }
    setBusy(true);
    try {
      await apiCall(`/api/v1/deployments/${deploymentId}/env/${n}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value }),
      });
      setName("");
      setValue("");
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function handleRemove(n: string) {
    await apiCall(`/api/v1/deployments/${deploymentId}/env/${n}`, {
      method: "DELETE",
    });
    router.refresh();
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Environment variables</CardTitle>
        <CardDescription>
          Injected into the deployment script&apos;s environment at run time.
          Values are write-only — they can be replaced or removed, never read
          back. Occurrences in the run log are masked best-effort (transformed
          values can&apos;t be caught).
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {names.length === 0 ? (
          <EmptyState
            icon={KeyRound}
            title="No variables"
            description="Add the DB passwords and API keys the script needs — without pasting them into the script text."
          />
        ) : (
          <ul className="grid gap-1.5">
            {names.map((n) => (
              <li
                key={n}
                className="flex items-center justify-between gap-3 rounded-md border px-3 py-2 text-sm"
              >
                <span className="flex min-w-0 items-center gap-2">
                  <span className="truncate font-mono">{n}</span>
                  <span className="text-xs text-muted-foreground">•••• set</span>
                </span>
                <span className="flex shrink-0 items-center gap-1">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      setName(n);
                      setValue("");
                      setError(null);
                    }}
                  >
                    Replace
                  </Button>
                  <ConfirmDialog
                    trigger={
                      <Button variant="ghost" size="sm">
                        <Trash2 className="size-3.5" />
                      </Button>
                    }
                    title={`Remove ${n}?`}
                    description="The stored value is deleted. Scripts referencing it will see it unset on the next run."
                    confirmLabel="Remove variable"
                    onConfirm={() => handleRemove(n)}
                  />
                </span>
              </li>
            ))}
          </ul>
        )}

        <form onSubmit={handleSave} className="grid grid-cols-[1fr_1.5fr_auto] items-end gap-2">
          <div className="grid gap-2">
            <Label htmlFor="env-name">Name</Label>
            <Input
              id="env-name"
              placeholder="DATABASE_URL"
              className="font-mono"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="env-value">Value</Label>
            <Input
              id="env-value"
              type="password"
              autoComplete="new-password"
              placeholder={isReplace ? "New value (replaces the stored one)" : "Secret value"}
              value={value}
              onChange={(e) => setValue(e.target.value)}
            />
          </div>
          <Button type="submit" disabled={busy || !name.trim()}>
            {busy && <Loader2 className="size-4 animate-spin" />}
            {isReplace ? "Replace" : "Add"}
          </Button>
        </form>
        {error && <p className="text-sm font-medium text-destructive">{error}</p>}
      </CardContent>
    </Card>
  );
}
