"use client";

import { useState } from "react";
import { Check, Copy, KeyRound, Loader2, UserPlus } from "lucide-react";
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
import { ROLES, ROLE_LABELS, type Role } from "@/lib/permissions";

// One-time reveal of a generated password. Shown once; never fetched again.
export function PasswordReveal({ email, password }: { email: string; password: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    await navigator.clipboard.writeText(password);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }
  return (
    <div className="grid gap-2 rounded-md border border-amber-500/40 bg-amber-500/5 p-3">
      <p className="text-sm font-medium">One-time password for {email}</p>
      <div className="flex items-center gap-2">
        <code className="flex-1 truncate rounded bg-muted px-2 py-1 font-mono text-sm">
          {password}
        </code>
        <Button type="button" variant="outline" size="sm" onClick={copy}>
          {copied ? <Check className="size-4" /> : <Copy className="size-4" />}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        Copy and share this securely — it won&apos;t be shown again. The user
        must change it on first login.
      </p>
    </div>
  );
}

export function ProvisionMemberDialog({
  orgId,
  onChanged,
}: {
  orgId: string;
  onChanged: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [role, setRole] = useState<Role>("developer");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ email: string; password: string } | null>(null);

  function reset() {
    setEmail("");
    setDisplayName("");
    setRole("developer");
    setError(null);
    setResult(null);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!email.trim()) return;
    setBusy(true);
    try {
      const data = await apiFetch(`/api/v1/orgs/${orgId}/members/provision`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: email.trim(),
          display_name: displayName.trim() || null,
          role,
        }),
      });
      setResult({ email: data.email, password: data.password });
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) reset();
      }}
    >
      <DialogTrigger render={<Button variant="outline" size="sm" />}>
        <UserPlus className="size-4" />
        Add User
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Provision a user</DialogTitle>
          <DialogDescription>
            Creates the account with a generated one-time password. Share it
            offline — the app sends nothing.
          </DialogDescription>
        </DialogHeader>

        {result ? (
          <div className="grid gap-4">
            <PasswordReveal email={result.email} password={result.password} />
            <DialogFooter>
              <Button type="button" variant="outline" onClick={reset}>
                Provision another
              </Button>
              <Button type="button" onClick={() => setOpen(false)}>
                Done
              </Button>
            </DialogFooter>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="grid gap-4">
            <div className="grid gap-2">
              <Label htmlFor="provision-email">Email</Label>
              <Input
                id="provision-email"
                type="email"
                placeholder="teammate@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="provision-name">Display name</Label>
              <Input
                id="provision-name"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="Optional"
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="provision-role">Role</Label>
              <Select
                items={ROLES.map((r) => ({ value: r, label: ROLE_LABELS[r] }))}
                value={role}
                onValueChange={(v) => {
                  if (typeof v === "string") setRole(v as Role);
                }}
              >
                <SelectTrigger id="provision-role" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {ROLES.map((r) => (
                    <SelectItem key={r} value={r}>
                      {ROLE_LABELS[r]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {error && <p className="text-sm font-medium text-destructive">{error}</p>}
            <DialogFooter>
              <Button type="submit" disabled={busy}>
                {busy ? <Loader2 className="size-4 animate-spin" /> : <KeyRound className="size-4" />}
                Create &amp; generate password
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
