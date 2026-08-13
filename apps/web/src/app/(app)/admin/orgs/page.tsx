"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Archive,
  ArchiveRestore,
  ChevronLeft,
  ChevronRight,
  Loader2,
  MoreHorizontal,
  Plus,
  Search,
  Trash2,
} from "lucide-react";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { ConfirmDialog } from "@/components/confirm-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

type AdminOrg = {
  id: string;
  name: string;
  shortname: string;
  archived_at: string | null;
  is_platform_admin: boolean;
  created_at: string;
  organization_members: { count: number }[];
  owner: { email: string | null; display_name: string | null } | null;
  // US-57.2: each org's agent quota and how much of it is spent.
  max_agents: number;
  agent_count: number;
};

type OrgMember = {
  principal_id: string;
  kind: "human" | "agent" | string;
  email: string | null;
  display_name: string | null;
  role: string;
  joined_at: string;
};

const PAGE_SIZE = 10;
const MEMBER_PAGE_SIZE = 20;

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export default function AdminOrgsPage() {
  const [orgs, setOrgs] = useState<AdminOrg[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);

  // Members dialog: which org it's open for (null = closed), its fetched
  // roster, and the dialog's own search/pagination — separate from the org
  // table's, since an org can have hundreds of members (usually agent
  // workers from load-testing) with nowhere else to see who they are.
  const [viewingOrg, setViewingOrg] = useState<AdminOrg | null>(null);
  const [viewKind, setViewKind] = useState<"all" | "agent">("all");
  const [members, setMembers] = useState<OrgMember[] | null>(null);
  const [membersError, setMembersError] = useState<string | null>(null);
  const [memberQuery, setMemberQuery] = useState("");
  const [memberPage, setMemberPage] = useState(0);

  function openMembers(org: AdminOrg) {
    setViewingOrg(org);
    setViewKind("all");
  }

  function openAgents(org: AdminOrg) {
    setViewingOrg(org);
    setViewKind("agent");
  }

  useEffect(() => {
    if (!viewingOrg) return;
    setMembers(null);
    setMembersError(null);
    setMemberQuery("");
    setMemberPage(0);
    apiFetch(`/api/v1/admin/orgs/${viewingOrg.id}/members`)
      .then(setMembers)
      .catch((e) => setMembersError((e as Error).message));
  }, [viewingOrg]);

  async function load() {
    try {
      const data = await apiFetch("/api/v1/admin/orgs");
      setOrgs(data);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  useEffect(() => {
    load();
  }, []);

  // A search that changes the result set out from under the current page
  // number would strand the user on a page that no longer exists.
  useEffect(() => {
    setPage(0);
  }, [query]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!newName.trim()) return;
    setCreating(true);
    setError(null);
    try {
      await apiFetch("/api/v1/admin/orgs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: newName.trim() }),
      });
      setNewName("");
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setCreating(false);
    }
  }

  async function handleArchiveToggle(org: AdminOrg) {
    setBusyId(org.id);
    setError(null);
    try {
      await apiFetch(`/api/v1/admin/orgs/${org.id}/archive`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ archived: !org.archived_at }),
      });
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusyId(null);
    }
  }

  async function handleDelete(orgId: string, force = false) {
    await apiFetch(`/api/v1/admin/orgs/${orgId}${force ? "?force=true" : ""}`, {
      method: "DELETE",
    });
    await load();
  }

  async function handleRename(org: AdminOrg) {
    const name = window.prompt("New organization name", org.name);
    if (!name || !name.trim() || name.trim() === org.name) return;
    setBusyId(org.id);
    setError(null);
    try {
      await apiFetch(`/api/v1/admin/orgs/${org.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim() }),
      });
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusyId(null);
    }
  }

  async function handleEditSlug(org: AdminOrg) {
    const next = window.prompt(
      "New org slug — lowercase letters, numbers, and hyphens (max 24).\n\n" +
        "Warning: the slug addresses this org's git remotes (…/git/<slug>/<project>.git). " +
        "Changing it breaks any clone URLs already configured with the old slug until they're re-pointed.",
      org.shortname,
    );
    if (next === null) return;
    const slug = next.trim().toLowerCase();
    if (!slug || slug === org.shortname) return;
    setBusyId(org.id);
    setError(null);
    try {
      await apiFetch(`/api/v1/admin/orgs/${org.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ shortname: slug }),
      });
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusyId(null);
    }
  }

  async function handleEditMaxAgents(org: AdminOrg) {
    const next = window.prompt(
      "Agent quota for this org — how many agents it may create (1–100000). " +
        "Lowering it never removes an existing agent; it only blocks new creation.",
      String(org.max_agents),
    );
    if (next === null) return;
    const n = Number(next.trim());
    if (!Number.isInteger(n) || n < 1 || n > 100000 || n === org.max_agents) return;
    setBusyId(org.id);
    setError(null);
    try {
      await apiFetch(`/api/v1/admin/orgs/${org.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ max_agents: n }),
      });
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusyId(null);
    }
  }

  const filtered = useMemo(() => {
    if (!orgs) return [];
    const q = query.trim().toLowerCase();
    if (!q) return orgs;
    return orgs.filter((org) => {
      const owner = `${org.owner?.display_name ?? ""} ${org.owner?.email ?? ""}`;
      return (
        org.name.toLowerCase().includes(q) ||
        org.shortname.toLowerCase().includes(q) ||
        org.id.toLowerCase().includes(q) ||
        owner.toLowerCase().includes(q)
      );
    });
  }, [orgs, query]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const clampedPage = Math.min(page, pageCount - 1);
  const paged = filtered.slice(clampedPage * PAGE_SIZE, clampedPage * PAGE_SIZE + PAGE_SIZE);

  const filteredMembers = useMemo(() => {
    if (!members) return [];
    const byKind = viewKind === "agent" ? members.filter((m) => m.kind === "agent") : members;
    const q = memberQuery.trim().toLowerCase();
    if (!q) return byKind;
    return byKind.filter((m) => {
      const name = `${m.display_name ?? ""} ${m.email ?? ""} ${m.role} ${m.kind}`;
      return name.toLowerCase().includes(q);
    });
  }, [members, memberQuery, viewKind]);

  const memberPageCount = Math.max(1, Math.ceil(filteredMembers.length / MEMBER_PAGE_SIZE));
  const clampedMemberPage = Math.min(memberPage, memberPageCount - 1);
  const pagedMembers = filteredMembers.slice(
    clampedMemberPage * MEMBER_PAGE_SIZE,
    clampedMemberPage * MEMBER_PAGE_SIZE + MEMBER_PAGE_SIZE,
  );

  return (
    <div className="flex w-full flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Organizations</h1>
        <p className="text-sm text-muted-foreground">Every org on the platform.</p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <form onSubmit={handleCreate} className="flex gap-2">
          <Input
            placeholder="New org name"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
          />
          <Button type="submit" disabled={creating}>
            {creating ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />}
            Create
          </Button>
        </form>
        <div className="relative ml-auto w-full max-w-xs">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Search name, slug, owner, ID…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="pl-8"
          />
        </div>
      </div>

      {error && <p className="text-sm font-medium text-destructive">{error}</p>}

      {!orgs ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : filtered.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          {query ? `No orgs match "${query}".` : "No orgs yet."}
        </p>
      ) : (
        <>
          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Slug</TableHead>
                  <TableHead>Owner</TableHead>
                  <TableHead className="text-right">Members</TableHead>
                  <TableHead className="text-right">Agents</TableHead>
                  <TableHead className="w-10" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {paged.map((org) => (
                  <TableRow key={org.id}>
                    <TableCell>
                      <span className="flex flex-col gap-0.5">
                        <span className="font-medium">
                          {org.name}
                          {org.is_platform_admin && (
                            <span className="ml-2 text-xs text-muted-foreground">
                              (platform admin)
                            </span>
                          )}
                          {org.archived_at && (
                            <span className="ml-2 text-xs text-muted-foreground">(archived)</span>
                          )}
                        </span>
                        <span className="text-xs text-muted-foreground">
                          Created {formatDate(org.created_at)}
                        </span>
                      </span>
                    </TableCell>
                    <TableCell>
                      <code className="font-mono text-xs">{org.shortname}</code>
                    </TableCell>
                    <TableCell>
                      <span className="flex flex-col gap-0.5 text-xs">
                        <span>{org.owner?.display_name || org.owner?.email || "—"}</span>
                        {org.owner?.display_name && org.owner?.email && (
                          <span className="text-muted-foreground">{org.owner.email}</span>
                        )}
                      </span>
                    </TableCell>
                    <TableCell className="text-right">
                      <button
                        type="button"
                        className="underline decoration-dotted underline-offset-2 hover:text-foreground"
                        onClick={() => openMembers(org)}
                        title="View members"
                      >
                        {org.organization_members?.[0]?.count ?? 0}
                      </button>
                    </TableCell>
                    <TableCell className="text-right">
                      <button
                        type="button"
                        onClick={() => openAgents(org)}
                        title="View agents"
                        className={
                          "underline decoration-dotted underline-offset-2 hover:text-foreground" +
                          (org.agent_count >= org.max_agents
                            ? " font-medium text-amber-600 dark:text-amber-400"
                            : "")
                        }
                      >
                        {org.agent_count} / {org.max_agents}
                      </button>
                    </TableCell>
                    <TableCell>
                      <span className="flex items-center justify-end gap-1">
                        {!org.is_platform_admin && (
                          <>
                            <Button
                              variant="ghost"
                              size="icon"
                              title={org.archived_at ? "Restore" : "Archive"}
                              disabled={busyId === org.id}
                              onClick={() => handleArchiveToggle(org)}
                            >
                              {busyId === org.id ? (
                                <Loader2 className="size-4 animate-spin" />
                              ) : org.archived_at ? (
                                <ArchiveRestore className="size-4" />
                              ) : (
                                <Archive className="size-4" />
                              )}
                            </Button>
                            <ConfirmDialog
                              trigger={
                                <Button variant="ghost" size="icon" title="Delete">
                                  <Trash2 className="size-4" />
                                </Button>
                              }
                              title={`Delete "${org.name}"?`}
                              description="This permanently deletes the org and all of its projects, tasks, and members. This can't be undone."
                              confirmLabel="Delete org"
                              onConfirm={() => handleDelete(org.id)}
                              onForceConfirm={() => handleDelete(org.id, true)}
                              forceConfirmLabel="Force delete org"
                            />
                          </>
                        )}
                        <DropdownMenu>
                          <DropdownMenuTrigger
                            render={<Button variant="ghost" size="icon" disabled={busyId === org.id} />}
                          >
                            <MoreHorizontal className="size-4" />
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end" className="w-40">
                            <DropdownMenuItem onClick={() => handleRename(org)}>
                              Rename
                            </DropdownMenuItem>
                            <DropdownMenuItem onClick={() => handleEditSlug(org)}>
                              Edit slug
                            </DropdownMenuItem>
                            <DropdownMenuItem onClick={() => handleEditMaxAgents(org)}>
                              Edit quota
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </span>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>

          <div className="flex items-center justify-between text-sm text-muted-foreground">
            <span>
              {filtered.length} org{filtered.length === 1 ? "" : "s"}
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

      <Dialog open={!!viewingOrg} onOpenChange={(o) => !o && setViewingOrg(null)}>
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>
              {viewingOrg?.name} — {viewKind === "agent" ? "agents" : "members"}
            </DialogTitle>
            <DialogDescription>
              {viewKind === "agent"
                ? "Every agent principal with a membership row in this org."
                : "Every principal (human or agent) with a membership row in this org."}
            </DialogDescription>
          </DialogHeader>

          {membersError && (
            <p className="text-sm font-medium text-destructive">{membersError}</p>
          )}

          {!membersError && !members ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : !membersError && members ? (
            <div className="flex flex-col gap-3">
              <div className="relative w-full max-w-xs">
                <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  placeholder="Search name, email, role…"
                  value={memberQuery}
                  onChange={(e) => {
                    setMemberQuery(e.target.value);
                    setMemberPage(0);
                  }}
                  className="pl-8"
                />
              </div>

              {filteredMembers.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  {memberQuery
                    ? `No ${viewKind === "agent" ? "agents" : "members"} match "${memberQuery}".`
                    : viewKind === "agent"
                      ? "No agents."
                      : "No members."}
                </p>
              ) : (
                <>
                  <div className="max-h-96 overflow-y-auto rounded-md border">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Name</TableHead>
                          <TableHead>Kind</TableHead>
                          <TableHead>Role</TableHead>
                          <TableHead>Joined</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {pagedMembers.map((m) => (
                          <TableRow key={m.principal_id}>
                            <TableCell>
                              <span className="flex flex-col gap-0.5">
                                <span className="font-medium">
                                  {m.display_name || m.email || "—"}
                                </span>
                                {m.display_name && m.email && (
                                  <span className="text-xs text-muted-foreground">
                                    {m.email}
                                  </span>
                                )}
                              </span>
                            </TableCell>
                            <TableCell>
                              <Badge variant={m.kind === "agent" ? "secondary" : "outline"}>
                                {m.kind}
                              </Badge>
                            </TableCell>
                            <TableCell className="text-xs">{m.role}</TableCell>
                            <TableCell className="text-xs text-muted-foreground">
                              {formatDate(m.joined_at)}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>

                  <div className="flex items-center justify-between text-sm text-muted-foreground">
                    <span>
                      {filteredMembers.length}{" "}
                      {viewKind === "agent" ? "agent" : "member"}
                      {filteredMembers.length === 1 ? "" : "s"}
                      {memberQuery ? ` matching "${memberQuery}"` : ""} · page{" "}
                      {clampedMemberPage + 1} of {memberPageCount}
                    </span>
                    <div className="flex items-center gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={clampedMemberPage === 0}
                        onClick={() => setMemberPage((p) => Math.max(0, p - 1))}
                      >
                        <ChevronLeft className="size-4" />
                        Prev
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={clampedMemberPage >= memberPageCount - 1}
                        onClick={() =>
                          setMemberPage((p) => Math.min(memberPageCount - 1, p + 1))
                        }
                      >
                        Next
                        <ChevronRight className="size-4" />
                      </Button>
                    </div>
                  </div>
                </>
              )}
            </div>
          ) : null}
        </DialogContent>
      </Dialog>
    </div>
  );
}
