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
  -- A direct delete/update on this table fires this trigger at depth 1
  -- (pg_trigger_depth() counts the currently-firing trigger itself, so it
  -- is already >= 1 inside this function body even for a top-level
  -- statement — it is NOT 0 in that case). A cascade delete from removing
  -- the parent org (US-1.27, not yet built) goes through the FK's own
  -- constraint trigger first, nesting one level deeper to depth 2, and
  -- must be allowed through unconditionally there, since the org itself
  -- is going away too — otherwise no org with exactly one owner (the
  -- common case) could ever be hard-deleted. Verified empirically against
  -- the live DB: a `> 0` check let a direct delete of a sole owner
  -- succeed (bug); `> 1` correctly distinguishes "direct" from "cascade".
  if pg_trigger_depth() > 1 then
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
