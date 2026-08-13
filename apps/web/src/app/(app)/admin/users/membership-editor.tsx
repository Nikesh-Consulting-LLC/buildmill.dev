"use client";

import { useEffect, useState } from "react";
import { Loader2, Link2, Unlink } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ROLES, ROLE_LABELS, type Role } from "@/lib/permissions";

type OrgOption = { id: string; name: string };
// role arrives from the admin API as a plain string; narrowed to Role in the UI.
type Membership = { org_id: string; role: string; organizations: { name: string } | null };

export function MembershipEditor({
  userId,
  memberships,
  onChanged,
}: {
  userId: string;
  memberships: Membership[];
  onChanged: () => void;
}) {
  const [orgs, setOrgs] = useState<OrgOption[] | null>(null);
  const [selectedOrgId, setSelectedOrgId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiFetch("/api/v1/admin/orgs")
      .then((data: OrgOption[]) => setOrgs(data))
      .catch((e: Error) => setError(e.message));
  }, []);

  async function handleLink() {
    if (!selectedOrgId) return;
    setBusy(true);
    setError(null);
    try {
      await apiFetch("/api/v1/admin/memberships", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ org_id: selectedOrgId, user_id: userId, role: "developer" }),
      });
      setSelectedOrgId("");
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function handleRoleChange(m: Membership, nextRole: Role) {
    if (nextRole === m.role) return;
    setBusy(true);
    setError(null);
    try {
      await apiFetch("/api/v1/admin/memberships", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ org_id: m.org_id, user_id: userId, role: nextRole }),
      });
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function handleUnlink(orgId: string) {
    setBusy(true);
    setError(null);
    try {
      await apiFetch(
        `/api/v1/admin/memberships?org_id=${encodeURIComponent(orgId)}&user_id=${encodeURIComponent(userId)}`,
        { method: "DELETE" }
      );
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const unlinkedOrgs = (orgs ?? []).filter(
    (o) => !memberships.some((m) => m.org_id === o.id)
  );

  return (
    <div className="mt-2 grid gap-2">
      <ul className="grid gap-1">
        {memberships.map((m) => (
          <li key={m.org_id} className="flex items-center justify-between gap-2 text-xs">
            <span className="truncate">{m.organizations?.name ?? m.org_id}</span>
            <span className="flex items-center gap-1">
              <Select
                items={ROLES.map((r) => ({ value: r, label: ROLE_LABELS[r] }))}
                value={m.role}
                onValueChange={(v) => {
                  if (typeof v === "string") handleRoleChange(m, v as Role);
                }}
                disabled={busy}
              >
                <SelectTrigger className="h-8 w-28 text-xs">
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
              <Button variant="outline" size="sm" disabled={busy} onClick={() => handleUnlink(m.org_id)}>
                <Unlink className="size-3" />
              </Button>
            </span>
          </li>
        ))}
      </ul>
      <div className="flex items-center gap-2">
        <Select
          items={unlinkedOrgs.map((o) => ({ value: o.id, label: o.name }))}
          value={selectedOrgId}
          onValueChange={(v) => typeof v === "string" && setSelectedOrgId(v)}
        >
          <SelectTrigger className="h-8 max-w-56 text-xs">
            <SelectValue placeholder="Link to org…" />
          </SelectTrigger>
          <SelectContent>
            {unlinkedOrgs.map((o) => (
              <SelectItem key={o.id} value={o.id}>
                {o.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button variant="outline" size="sm" disabled={busy || !selectedOrgId} onClick={handleLink}>
          {busy ? <Loader2 className="size-3 animate-spin" /> : <Link2 className="size-3" />}
          Link
        </Button>
      </div>
      {error && <p className="text-xs font-medium text-destructive">{error}</p>}
    </div>
  );
}
