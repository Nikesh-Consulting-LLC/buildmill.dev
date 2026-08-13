"use client";

import { useEffect, useMemo, useState } from "react";
import { Ban, ChevronLeft, ChevronRight, CheckCircle2, Loader2, Search, Trash2 } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ConfirmDialog } from "@/components/confirm-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { EditUserDialog } from "./edit-user-dialog";
import { ResetPasswordDialog } from "./reset-password-dialog";
import { MembershipEditor } from "./membership-editor";

type AdminUser = {
  id: string;
  email: string;
  display_name: string | null;
  created_at: string;
  organization_members: {
    org_id: string;
    role: string;
    organizations: { name: string } | null;
  }[];
};

const PAGE_SIZE = 10;

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export default function AdminUsersPage() {
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const [viewingUser, setViewingUser] = useState<AdminUser | null>(null);

  async function load() {
    try {
      const data = await apiFetch("/api/v1/admin/users");
      setUsers(data);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  useEffect(() => {
    load();
  }, []);

  useEffect(() => {
    setPage(0);
  }, [query]);

  // Keep the open dialog's data in sync with the freshly-loaded list, and
  // close it if the user's membership row was just deleted out from under it.
  useEffect(() => {
    if (!viewingUser || !users) return;
    const fresh = users.find((u) => u.id === viewingUser.id);
    setViewingUser(fresh ?? null);
  }, [users, viewingUser?.id]);

  async function handleDelete(userId: string, force = false) {
    await apiFetch(`/api/v1/admin/users/${userId}${force ? "?force=true" : ""}`, {
      method: "DELETE",
    });
    await load();
  }

  async function handleDeactivateToggle(user: AdminUser, deactivated: boolean) {
    setBusyId(user.id);
    setError(null);
    try {
      await apiFetch(`/api/v1/admin/users/${user.id}/deactivate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ deactivated }),
      });
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusyId(null);
    }
  }

  const filtered = useMemo(() => {
    if (!users) return [];
    const q = query.trim().toLowerCase();
    if (!q) return users;
    return users.filter((u) => {
      const orgs = u.organization_members
        .map((m) => `${m.organizations?.name ?? m.org_id} ${m.role}`)
        .join(" ");
      return (
        (u.display_name ?? "").toLowerCase().includes(q) ||
        u.email.toLowerCase().includes(q) ||
        u.id.toLowerCase().includes(q) ||
        orgs.toLowerCase().includes(q)
      );
    });
  }, [users, query]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const clampedPage = Math.min(page, pageCount - 1);
  const paged = filtered.slice(clampedPage * PAGE_SIZE, clampedPage * PAGE_SIZE + PAGE_SIZE);

  return (
    <div className="flex w-full flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Users</h1>
        <p className="text-sm text-muted-foreground">Every user on the platform.</p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative ml-auto w-full max-w-xs">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Search name, email, org, ID…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="pl-8"
          />
        </div>
      </div>

      {error && <p className="text-sm font-medium text-destructive">{error}</p>}

      {!users ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : filtered.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          {query ? `No users match "${query}".` : "No users yet."}
        </p>
      ) : (
        <>
          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Email</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead className="text-right">Orgs</TableHead>
                  <TableHead className="w-10" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {paged.map((u) => (
                  <TableRow key={u.id}>
                    <TableCell>
                      <span className="font-medium">{u.display_name || "—"}</span>
                    </TableCell>
                    <TableCell>
                      <span className="text-xs">{u.email}</span>
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {formatDate(u.created_at)}
                    </TableCell>
                    <TableCell className="text-right">
                      <button
                        type="button"
                        className="underline decoration-dotted underline-offset-2 hover:text-foreground"
                        onClick={() => setViewingUser(u)}
                        title="View org memberships"
                      >
                        {u.organization_members.length}
                      </button>
                    </TableCell>
                    <TableCell>
                      <span className="flex items-center justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="icon"
                          title="Deactivate"
                          disabled={busyId === u.id}
                          onClick={() => handleDeactivateToggle(u, true)}
                        >
                          {busyId === u.id ? (
                            <Loader2 className="size-4 animate-spin" />
                          ) : (
                            <Ban className="size-4" />
                          )}
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          title="Reactivate"
                          disabled={busyId === u.id}
                          onClick={() => handleDeactivateToggle(u, false)}
                        >
                          {busyId === u.id ? (
                            <Loader2 className="size-4 animate-spin" />
                          ) : (
                            <CheckCircle2 className="size-4" />
                          )}
                        </Button>
                        <EditUserDialog user={u} onSaved={load} />
                        <ResetPasswordDialog userId={u.id} />
                        <ConfirmDialog
                          trigger={
                            <Button variant="ghost" size="icon" title="Delete">
                              <Trash2 className="size-4" />
                            </Button>
                          }
                          title={`Delete "${u.display_name || u.email}"?`}
                          description="This permanently deletes the user's login and removes them from every org they belong to. This can't be undone."
                          confirmLabel="Delete user"
                          onConfirm={() => handleDelete(u.id)}
                          onForceConfirm={() => handleDelete(u.id, true)}
                          forceConfirmLabel="Force delete user"
                        />
                      </span>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>

          <div className="flex items-center justify-between text-sm text-muted-foreground">
            <span>
              {filtered.length} user{filtered.length === 1 ? "" : "s"}
              {query ? ` matching "${query}"` : ""} · page {clampedPage + 1} of {pageCount}
            </span>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={clampedPage === 0}
                onClick={() => setPage((p) => Math.max(0, p - 1))}
              >
                <ChevronLeft className="size-4" />
                Prev
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={clampedPage >= pageCount - 1}
                onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
              >
                Next
                <ChevronRight className="size-4" />
              </Button>
            </div>
          </div>
        </>
      )}

      <Dialog open={!!viewingUser} onOpenChange={(o) => !o && setViewingUser(null)}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{viewingUser?.display_name || viewingUser?.email} — orgs</DialogTitle>
            <DialogDescription>
              Every org this user belongs to, and their role in each.
            </DialogDescription>
          </DialogHeader>
          {viewingUser && (
            <MembershipEditor
              userId={viewingUser.id}
              memberships={viewingUser.organization_members}
              onChanged={load}
            />
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
