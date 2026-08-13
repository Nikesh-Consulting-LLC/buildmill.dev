# Org Membership Management (us-1.26) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an org owner add an existing Software Factory user to their org by email, remove a member, and change a member's role — with a guard so an org can never end up with zero owners.

**Architecture:** Everything is plain Supabase-SDK CRUD under RLS/RPC from `apps/web` — no new FastAPI surface. A new `security definer` RPC (`add_org_member_by_email`) does the privileged email→user-id lookup an owner can't do directly (client code has no read access to `auth.users`). New RLS policies widen `organization_members` visibility to the whole org roster (read-only for non-owners) and grant owners write access. A `BEFORE DELETE/UPDATE` trigger blocks removing or demoting an org's last remaining owner — written from the start to tolerate a cascade delete from the parent `organizations` row (needed later by [us-1.27](2026-07-13-superadmin-platform-console.md), which depends on this story), even though org deletion doesn't exist yet in this story.

**Tech Stack:** Next.js 16 App Router, Supabase JS SDK, Postgres RLS + `security definer` functions + trigger.

## Global Constraints

- Migration goes in `infra/supabase/migrations/`, next free number is **`014`** (confirm via `list_migrations` before running — `013_project_issue_sync.sql` is the last one in this worktree's history). Apply via MCP `apply_migration`, then regenerate `apps/web/src/lib/supabase/database.types.ts` via MCP `generate_typescript_types`.
- No test runner exists for `apps/web`. Steps replace "test-first" with "implement → `npm run build` → manual browser check → commit." The RPC/trigger/RLS behavior is verified via direct SQL through the Supabase MCP (`execute_sql`), the same substitution used in the prior us-1.24/us-1.25 work — say so plainly rather than claiming pytest/vitest coverage this repo has no harness for.
- shadcn here is Base UI: triggers use `render={<Button />}`, not `asChild`.
- No comments in code unless explaining a genuinely non-obvious constraint (the `pg_trigger_depth()` guard in Task 1 IS one and gets a short comment explaining why).
- Never add an insert RLS policy for `organization_members` — inserts happen only through the `add_org_member_by_email` RPC (`security definer`, runs as table owner, bypasses RLS the same way `set_llm_api_key` in `002_llm_settings.sql` does). Adding a client-facing insert policy would let any org member insert arbitrary membership rows directly, bypassing the email-lookup gate.

---

### Task 1: Migration — RLS, `is_org_owner()`, `add_org_member_by_email()` RPC, last-owner guard trigger

**Files:**
- Create: `infra/supabase/migrations/014_org_membership.sql`
- Modify (regenerate): `apps/web/src/lib/supabase/database.types.ts`

**Interfaces:**
- Produces: `public.is_org_owner(org uuid) returns boolean` (mirrors `is_org_member`), `public.add_org_member_by_email(p_org uuid, p_email text) returns void` (RPC, `security definer`), trigger `organization_members_guard_last_owner`, and 3 new RLS policies on `organization_members` (broad select, owner-scoped update, owner-scoped delete).

- [ ] **Step 1: Write the migration**

```sql
-- 014_org_membership: add/remove/change-role for organization_members
-- (US-1.26). Today's RLS only lets a user see their own membership row;
-- this widens read access to the whole org roster and adds owner-scoped
-- write policies, an email-lookup RPC for adding members (client code
-- has no read access to auth.users), and a trigger guarding against an
-- org ending up with zero owners.

create or replace function public.is_org_owner(org uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1
    from public.organization_members m
    where m.org_id = org
      and m.user_id = (select auth.uid())
      and m.role = 'owner'
  );
$$;

create policy "members can view their org's roster"
  on public.organization_members for select
  using (public.is_org_member(org_id));

create policy "owners can update their org's membership rows"
  on public.organization_members for update
  using (public.is_org_owner(org_id))
  with check (public.is_org_owner(org_id));

create policy "owners can remove their org's membership rows"
  on public.organization_members for delete
  using (public.is_org_owner(org_id));

create or replace function public.add_org_member_by_email(p_org uuid, p_email text)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user_id uuid;
begin
  if not public.is_org_owner(p_org) then
    raise exception 'not authorized';
  end if;

  select id into v_user_id from auth.users where lower(email) = lower(p_email) limit 1;
  if v_user_id is null then
    raise exception 'No account found for that email — ask them to sign up first, then add them again.';
  end if;

  if exists (
    select 1 from public.organization_members
    where org_id = p_org and user_id = v_user_id
  ) then
    raise exception 'That user is already a member of this organization.';
  end if;

  insert into public.organization_members (org_id, user_id, role)
  values (p_org, v_user_id, 'member');
end;
$$;

revoke execute on function public.add_org_member_by_email(uuid, text) from public, anon;
grant execute on function public.add_org_member_by_email(uuid, text) to authenticated;
revoke execute on function public.is_org_owner(uuid) from public, anon;
grant execute on function public.is_org_owner(uuid) to authenticated;

create or replace function public.guard_last_owner()
returns trigger
language plpgsql
as $$
begin
  -- pg_trigger_depth() = 0 only for a direct delete/update on this table.
  -- A cascade delete from removing the parent org (US-1.27, not yet built)
  -- runs at depth > 0 and must be allowed through unconditionally, since
  -- the org itself is going away too — otherwise no org with exactly one
  -- owner (the common case) could ever be hard-deleted.
  if pg_trigger_depth() > 0 then
    if TG_OP = 'DELETE' then
      return old;
    else
      return new;
    end if;
  end if;

  if TG_OP = 'DELETE' then
    if old.role = 'owner' and (
      select count(*) from public.organization_members
      where org_id = old.org_id and role = 'owner'
    ) <= 1 then
      raise exception 'Cannot remove the last remaining owner of this organization.';
    end if;
    return old;
  end if;

  if TG_OP = 'UPDATE' and old.role = 'owner' and new.role = 'member' then
    if (
      select count(*) from public.organization_members
      where org_id = old.org_id and role = 'owner'
    ) <= 1 then
      raise exception 'Cannot demote the last remaining owner of this organization.';
    end if;
  end if;
  return new;
end;
$$;

create trigger organization_members_guard_last_owner
  before delete or update on public.organization_members
  for each row execute function public.guard_last_owner();
```

- [ ] **Step 2: Apply the migration to the live Supabase project**

Use the Supabase MCP `apply_migration` tool, project id `wdudmfhhqxrqzoyhuzwx`, name `org_membership`, with the SQL above. Before applying, run `list_migrations` to confirm `014` is actually free (no other migration landed first).

- [ ] **Step 3: Regenerate TypeScript types**

Use the Supabase MCP `generate_typescript_types` tool for project `wdudmfhhqxrqzoyhuzwx` and overwrite `apps/web/src/lib/supabase/database.types.ts`.

- [ ] **Step 4: Verify with direct SQL**

Use the Supabase MCP `execute_sql` tool. Find or create a real test org with two members (one owner, one member) — or use an existing org/user pair from `list_tables`/a `select` query — and verify:

```sql
-- as a sanity check (service-role execute_sql bypasses RLS, so this just
-- confirms the trigger/RPC logic, not RLS itself):

-- 1. Removing a sole owner directly should raise.
-- (pick a real org_id/user_id pair where role='owner' and it's the only owner)
delete from public.organization_members where org_id = '<org_id>' and user_id = '<owner_user_id>';
-- expect: ERROR — Cannot remove the last remaining owner of this organization.

-- 2. Demoting a sole owner directly should raise.
update public.organization_members set role = 'member' where org_id = '<org_id>' and user_id = '<owner_user_id>';
-- expect: ERROR — Cannot demote the last remaining owner of this organization.

-- 3. add_org_member_by_email with a real existing email should succeed
--    (skip if no second test account is available — note as unverified).

-- 4. add_org_member_by_email with a made-up email should raise.
select public.add_org_member_by_email('<org_id>', 'definitely-not-a-real-user@example.com');
-- expect: ERROR — No account found for that email...

-- 5. Cross-org isolation: simulate a real member of org A (not org B) and
-- confirm they can't see or act on org B's roster. Pick a second real org
-- (org B) with at least one member, and a user_id who is a member of org A
-- but NOT org B.
set local role authenticated;
select set_config('request.jwt.claims', json_build_object('sub', '<org_a_member_user_id>')::text, true);

select * from public.organization_members where org_id = '<org_b_id>';
-- expect: zero rows — RLS hides org B's roster from an org A member

select public.add_org_member_by_email('<org_b_id>', '<any_real_email>');
-- expect: ERROR — not authorized (org A member is not an owner of org B)

reset role;
```

- [ ] **Step 5: Commit**

```bash
git add infra/supabase/migrations/014_org_membership.sql apps/web/src/lib/supabase/database.types.ts
git commit -m "feat: add org membership RLS, add-member RPC, and last-owner guard"
```

---

### Task 2: Members settings section

**Files:**
- Create: `apps/web/src/app/(app)/settings/members-settings.tsx`
- Modify: `apps/web/src/app/(app)/settings/page.tsx`

**Interfaces:**
- Consumes: `add_org_member_by_email` RPC and `organization_members`/`profiles` tables (Task 1).
- Produces: `MembersSettings({ orgId, isOwner, members }: { orgId: string; isOwner: boolean; members: MemberRow[] })` — client component, nothing later depends on it.

- [ ] **Step 1: Fetch members + the caller's own role in `settings/page.tsx`**

In `apps/web/src/app/(app)/settings/page.tsx`, alongside the existing `membership`/`settings`/`installations` queries, add:

```tsx
const { data: members } = await supabase
  .from("organization_members")
  .select("user_id, role, created_at, profiles(email, display_name)")
  .eq("org_id", membership.org_id)
  .order("created_at", { ascending: true });

const { data: ownRole } = await supabase
  .from("organization_members")
  .select("role")
  .eq("org_id", membership.org_id)
  .eq("user_id", user.id)
  .maybeSingle();
```

Add a new `Card` after the GitHub one (import `MembersSettings` from `./members-settings`):

```tsx
<Card>
  <CardHeader>
    <CardTitle>Members</CardTitle>
    <CardDescription>
      People with access to this org's projects and tasks.
    </CardDescription>
  </CardHeader>
  <CardContent>
    <MembersSettings
      orgId={membership.org_id}
      isOwner={ownRole?.role === "owner"}
      members={(members ?? []) as MemberRow[]}
    />
  </CardContent>
</Card>
```

Import the `MemberRow` type from `./members-settings` for the cast above.

- [ ] **Step 2: `MembersSettings` component**

```tsx
// apps/web/src/app/(app)/settings/members-settings.tsx
"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2, UserMinus, UserPlus } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export type MemberRow = {
  user_id: string;
  role: "owner" | "member";
  created_at: string;
  profiles: { email: string; display_name: string | null } | null;
};

function formatWhen(iso: string) {
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function MembersSettings({
  orgId,
  isOwner,
  members,
}: {
  orgId: string;
  isOwner: boolean;
  members: MemberRow[];
}) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [adding, setAdding] = useState(false);
  const [busyUserId, setBusyUserId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!email.trim()) return;
    setAdding(true);
    const supabase = createClient();
    try {
      const { error: rpcError } = await supabase.rpc("add_org_member_by_email", {
        p_org: orgId,
        p_email: email.trim(),
      });
      if (rpcError) {
        setError(rpcError.message);
        return;
      }
      setEmail("");
      router.refresh();
    } finally {
      setAdding(false);
    }
  }

  async function handleRemove(userId: string) {
    if (!confirm("Remove this member from the organization?")) return;
    setError(null);
    setBusyUserId(userId);
    const supabase = createClient();
    try {
      const { error: dbError } = await supabase
        .from("organization_members")
        .delete()
        .eq("org_id", orgId)
        .eq("user_id", userId);
      if (dbError) {
        setError(dbError.message);
        return;
      }
      router.refresh();
    } finally {
      setBusyUserId(null);
    }
  }

  async function handleRoleToggle(userId: string, currentRole: "owner" | "member") {
    setError(null);
    setBusyUserId(userId);
    const supabase = createClient();
    try {
      const nextRole = currentRole === "owner" ? "member" : "owner";
      const { error: dbError } = await supabase
        .from("organization_members")
        .update({ role: nextRole })
        .eq("org_id", orgId)
        .eq("user_id", userId);
      if (dbError) {
        setError(dbError.message);
        return;
      }
      router.refresh();
    } finally {
      setBusyUserId(null);
    }
  }

  return (
    <div className="grid gap-4">
      <ul className="grid gap-2">
        {members.map((m) => (
          <li
            key={m.user_id}
            className="flex items-center justify-between gap-3 rounded-md border px-3 py-2 text-sm"
          >
            <span className="flex min-w-0 flex-col">
              <span className="truncate font-medium">
                {m.profiles?.display_name || m.profiles?.email || m.user_id}
              </span>
              <span className="truncate text-xs text-muted-foreground">
                {m.profiles?.email} · Joined {formatWhen(m.created_at)}
              </span>
            </span>
            <span className="flex shrink-0 items-center gap-2">
              {isOwner ? (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={busyUserId === m.user_id}
                  onClick={() => handleRoleToggle(m.user_id, m.role)}
                >
                  {busyUserId === m.user_id && <Loader2 className="size-4 animate-spin" />}
                  {m.role === "owner" ? "Owner" : "Member"}
                </Button>
              ) : (
                <span className="rounded-full border px-2.5 py-0.5 text-xs font-medium capitalize text-muted-foreground">
                  {m.role}
                </span>
              )}
              {isOwner && (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={busyUserId === m.user_id}
                  onClick={() => handleRemove(m.user_id)}
                >
                  <UserMinus className="size-4" />
                </Button>
              )}
            </span>
          </li>
        ))}
      </ul>

      {isOwner && (
        <form onSubmit={handleAdd} className="flex flex-col gap-2">
          <Label htmlFor="member-email">Add a member by email</Label>
          <div className="flex gap-2">
            <Input
              id="member-email"
              type="email"
              placeholder="teammate@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
            <Button type="submit" disabled={adding}>
              {adding ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <UserPlus className="size-4" />
              )}
              Add
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            They must already have a Software Factory account. There's no
            org switcher yet, so this only takes effect once one exists —
            they won't see this org's data by being added alone.
          </p>
        </form>
      )}

      {error && <p className="text-sm font-medium text-destructive">{error}</p>}
    </div>
  );
}
```

- [ ] **Step 3: Build check**

Run: `npm run build`.

- [ ] **Step 4: Manual verification**

In the browser preview: as an owner, add a member by an email that has no account and confirm the clear error; remove a member and confirm the list updates; toggle a member's role; attempt to remove/demote yourself when you're the org's only owner and confirm the guard's error message surfaces inline (not a raw 500).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/app/\(app\)/settings/members-settings.tsx apps/web/src/app/\(app\)/settings/page.tsx
git commit -m "feat: add Members settings section — add/remove/change-role by owners"
```
