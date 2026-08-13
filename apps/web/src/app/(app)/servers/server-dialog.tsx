"use client";

import { useRef, useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { CheckCircle2, Loader2, Pencil, Plug, Plus, Upload } from "lucide-react";
import { apiCall } from "@/lib/api";
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
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import type { ServerRow } from "./server-card";

type AuthMethod = "password" | "ssh_key";

export function ServerDialog({ orgId, server }: { orgId: string; server?: ServerRow }) {
  const router = useRouter();
  const isEdit = !!server;
  const fileRef = useRef<HTMLInputElement>(null);

  const [open, setOpen] = useState(false);
  const [name, setName] = useState(server?.name ?? "");
  const [host, setHost] = useState(server?.host ?? "");
  const [port, setPort] = useState(String(server?.port ?? 22));
  const [username, setUsername] = useState(server?.username ?? "");
  const [authMethod, setAuthMethod] = useState<AuthMethod>(server?.auth_method ?? "password");
  const [password, setPassword] = useState("");
  const [privateKey, setPrivateKey] = useState("");
  const [passphrase, setPassphrase] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // US-20.4: the dry-run result. Cleared by any connection-field edit, so a
  // stale green can never be read as describing what is currently typed.
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<
    { ok: true; fingerprint: string | null } | { ok: false; message: string } | null
  >(null);

  function reset() {
    setName("");
    setHost("");
    setPort("22");
    setUsername("");
    setAuthMethod("password");
    setPassword("");
    setPrivateKey("");
    setPassphrase("");
    setError(null);
    setTestResult(null);
  }

  /** Every control that changes what would be connected to clears the
   * previous verdict. */
  function edited<T>(setter: (v: T) => void) {
    return (value: T) => {
      setTestResult(null);
      setter(value);
    };
  }

  /** Shared by Save and Test — the same bar, the same messages. */
  function validate(): string | null {
    if (!name.trim() || !host.trim() || !username.trim()) {
      return "Name, host, and username are required.";
    }
    const portNum = Number(port);
    if (!Number.isInteger(portNum) || portNum < 1 || portNum > 65535) {
      return "Port must be between 1 and 65535.";
    }
    // On edit a blank credential means "keep the stored one", which the
    // dry-run resolves server-side; on create there is nothing to fall
    // back to.
    if (!isEdit && authMethod === "password" && !password) {
      return "Enter a password.";
    }
    if (!isEdit && authMethod === "ssh_key" && !privateKey.trim()) {
      return "Paste or upload a private key.";
    }
    return null;
  }

  async function handleTest() {
    setError(null);
    setTestResult(null);
    const invalid = validate();
    if (invalid) {
      setError(invalid);
      return;
    }
    setTesting(true);
    try {
      const hasNewCredential =
        authMethod === "password" ? !!password : !!privateKey.trim();
      let result: { host_key_fingerprint?: string | null };
      if (isEdit && !hasNewCredential) {
        // The stored credential path already exists and enforces the
        // trusted host key (us-1.28).
        result = (await apiCall(`/api/v1/servers/${server!.id}/test`, {
          method: "POST",
        })) as { host_key_fingerprint?: string | null };
      } else {
        result = (await apiCall("/api/v1/servers/test-connection", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            host: host.trim(),
            port: Number(port),
            username: username.trim(),
            auth_method: authMethod,
            ...(authMethod === "password"
              ? { password }
              : { private_key: privateKey, passphrase: passphrase || null }),
            ...(isEdit ? { server_id: server!.id } : {}),
          }),
        })) as { host_key_fingerprint?: string | null };
      }
      setTestResult({
        ok: true,
        fingerprint: result.host_key_fingerprint ?? null,
      });
    } catch (e) {
      setTestResult({ ok: false, message: (e as Error).message });
    } finally {
      setTesting(false);
    }
  }

  async function handleKeyFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    edited(setPrivateKey)(await file.text());
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    const invalid = validate();
    if (invalid) {
      setError(invalid);
      return;
    }
    const portNum = Number(port);
    // On edit, switching auth method requires the new credential.
    if (isEdit && authMethod !== server!.auth_method) {
      if (authMethod === "password" && !password) {
        setError("Enter a password for the new auth method.");
        return;
      }
      if (authMethod === "ssh_key" && !privateKey.trim()) {
        setError("Paste a private key for the new auth method.");
        return;
      }
    }

    setSaving(true);
    try {
      const payload: Record<string, unknown> = {
        name: name.trim(),
        host: host.trim(),
        port: portNum,
        username: username.trim(),
        auth_method: authMethod,
      };
      if (authMethod === "password") {
        if (password) payload.password = password;
      } else {
        if (privateKey.trim()) payload.private_key = privateKey;
        if (passphrase) payload.passphrase = passphrase;
      }

      if (isEdit) {
        await apiCall(`/api/v1/servers/${server!.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      } else {
        await apiCall("/api/v1/servers", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ...payload, org_id: orgId }),
        });
      }

      setOpen(false);
      if (!isEdit) reset();
      else {
        setPassword("");
        setPrivateKey("");
        setPassphrase("");
      }
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger
        render={
          isEdit ? <Button variant="outline" size="sm" /> : <Button variant="create" />
        }
      >
        {isEdit ? (
          <>
            <Pencil className="size-4" />
            Edit
          </>
        ) : (
          <>
            <Plus className="size-4" />
            New server
          </>
        )}
      </DialogTrigger>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit server" : "New server"}</DialogTitle>
          <DialogDescription>
            {isEdit
              ? "Update the server. Leave the credential blank to keep the current one."
              : "Register a deployment target. The credential is stored write-only — it's never shown again."}
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSave} className="grid gap-4">
          <div className="grid gap-2">
            <Label htmlFor="server-name">Name</Label>
            <Input id="server-name" placeholder="prod-web-1" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="grid grid-cols-[1fr_5rem] gap-2">
            <div className="grid gap-2">
              <Label htmlFor="server-host">Host</Label>
              <Input id="server-host" placeholder="1.2.3.4 or host.example.com" value={host} onChange={(e) => edited(setHost)(e.target.value)} />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="server-port">Port</Label>
              <Input id="server-port" inputMode="numeric" value={port} onChange={(e) => edited(setPort)(e.target.value)} />
            </div>
          </div>
          <div className="grid gap-2">
            <Label htmlFor="server-username">Username</Label>
            <Input id="server-username" placeholder="root" value={username} onChange={(e) => edited(setUsername)(e.target.value)} />
          </div>

          <div className="grid gap-2">
            <Label>Authentication</Label>
            <div className="inline-flex rounded-md border p-0.5">
              {(["password", "ssh_key"] as AuthMethod[]).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => edited(setAuthMethod)(m)}
                  className={cn(
                    "flex-1 rounded px-3 py-1.5 text-sm font-medium transition-colors",
                    authMethod === m
                      ? "bg-secondary text-secondary-foreground"
                      : "text-muted-foreground hover:text-foreground"
                  )}
                >
                  {m === "password" ? "Password" : "SSH key"}
                </button>
              ))}
            </div>
          </div>

          {authMethod === "password" ? (
            <div className="grid gap-2">
              <Label htmlFor="server-password">Password</Label>
              <Input
                id="server-password"
                type="password"
                autoComplete="new-password"
                placeholder={isEdit ? "Leave blank to keep current" : "••••••••"}
                value={password}
                onChange={(e) => edited(setPassword)(e.target.value)}
              />
            </div>
          ) : (
            <>
              <div className="grid gap-2">
                <div className="flex items-center justify-between">
                  <Label htmlFor="server-key">Private key</Label>
                  <Button type="button" variant="ghost" size="sm" onClick={() => fileRef.current?.click()}>
                    <Upload className="size-3.5" />
                    Upload
                  </Button>
                  <input ref={fileRef} type="file" className="hidden" onChange={handleKeyFile} />
                </div>
                <Textarea
                  id="server-key"
                  rows={6}
                  spellCheck={false}
                  className="h-40 resize-none overflow-auto font-mono text-xs field-sizing-fixed"
                  placeholder={
                    isEdit
                      ? "Leave blank to keep current key"
                      : "-----BEGIN OPENSSH PRIVATE KEY-----"
                  }
                  value={privateKey}
                  onChange={(e) => edited(setPrivateKey)(e.target.value)}
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="server-passphrase">Key passphrase (optional)</Label>
                <Input
                  id="server-passphrase"
                  type="password"
                  autoComplete="new-password"
                  value={passphrase}
                  onChange={(e) => edited(setPassphrase)(e.target.value)}
                />
              </div>
            </>
          )}

          {error && <p className="text-sm font-medium text-destructive">{error}</p>}

          {/* US-20.4: the verdict sits above the footer and never closes the
              dialog — the whole point is finding out before committing. */}
          {testResult &&
            (testResult.ok ? (
              <p className="flex flex-wrap items-center gap-1.5 rounded-md border border-emerald-600/30 bg-emerald-600/5 px-3 py-2 text-sm text-emerald-700 dark:text-emerald-400">
                <CheckCircle2 className="size-4 shrink-0" />
                Connected.
                {testResult.fingerprint && (
                  <span className="font-mono text-xs text-muted-foreground">
                    Host key {testResult.fingerprint}
                  </span>
                )}
              </p>
            ) : (
              <p className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm font-medium text-destructive">
                {testResult.message}
              </p>
            ))}

          <DialogFooter className="sm:justify-between">
            <Button
              type="button"
              variant="outline"
              disabled={testing || saving}
              onClick={handleTest}
            >
              {testing ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Plug className="size-4" />
              )}
              Test connection
            </Button>
            <Button type="submit" disabled={saving || testing}>
              {saving && <Loader2 className="size-4 animate-spin" />}
              {isEdit ? "Save changes" : "Create server"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
