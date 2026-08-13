-- 089_member_status_suspend: roster status, suspend, token cascade (US-9.6).
--
-- Suspension makes a principal's membership inactive. A suspended principal is
-- blocked from the org: the membership helpers now require status='active', so
-- all ~52 org-scoped policies exclude suspended members without per-policy
-- edits. Suspending or removing a principal also cascades to its worker tokens
-- (get_worker_by_token checks only workers.status, so we revoke them here),
-- cutting API + MCP + git access in the same action. The ">= 1 active owner"
-- invariant is enforced against demotion, suspension, and removal.

-- ---------------------------------------------------------------------------
-- Membership helpers require ACTIVE membership (suspended = blocked)
-- ---------------------------------------------------------------------------
create or replace function public.is_org_member(org uuid)
returns boolean language sql stable security definer set search_path = public
as $$
  select exists (
    select 1
    from public.organization_members m
    join public.principals pr on pr.id = m.principal_id
    where m.org_id = org
      and pr.auth_user_id = (select auth.uid())
      and m.status = 'active'
  );
$$;

create or replace function public.is_org_owner(org uuid)
returns boolean language sql stable security definer set search_path = public
as $$
  select exists (
    select 1
    from public.organization_members m
    join public.principals pr on pr.id = m.principal_id
    where m.org_id = org
      and pr.auth_user_id = (select auth.uid())
      and m.role = 'owner'
      and m.status = 'active'
  );
$$;

create or replace function public.has_org_capability(p_org uuid, p_capability text)
returns boolean language sql stable security definer set search_path = public
as $$
  select public.is_platform_admin() or exists (
    select 1
    from public.organization_members m
    join public.principals pr on pr.id = m.principal_id
    join public.role_capabilities rc
      on rc.role = m.role and rc.capability = p_capability
    where m.org_id = p_org
      and pr.auth_user_id = (select auth.uid())
      and m.status = 'active'
      and rc.allowed = true
  );
$$;

-- Explicit synonym for the intent "active membership" (US-9.6); is_org_member
-- now carries the same active check, this names it where readability helps.
create or replace function public.is_active_org_member(org uuid)
returns boolean language sql stable security definer set search_path = public
as $$
  select public.is_org_member(org);
$$;

revoke execute on function public.is_active_org_member(uuid) from public, anon;
grant execute on function public.is_active_org_member(uuid) to authenticated;

-- ---------------------------------------------------------------------------
-- >= 1 active owner: guard demotion, suspension, and removal (fixes the US-9.2
-- role rename that left the old new.role='member' branch dead)
-- ---------------------------------------------------------------------------
create or replace function public.guard_last_owner()
returns trigger language plpgsql as $$
declare
  active_owners int;
begin
  -- Cascade delete from removing the parent org nests one level deeper; let it
  -- through unconditionally (the org itself is going away).
  if pg_trigger_depth() > 1 then
    if TG_OP = 'DELETE' then
      return old;
    else
      return new;
    end if;
  end if;

  if TG_OP = 'DELETE' then
    if old.role = 'owner' and old.status = 'active' then
      select count(*) into active_owners
      from public.organization_members
      where org_id = old.org_id and role = 'owner' and status = 'active';
      if active_owners <= 1 then
        raise exception 'Cannot remove the last active owner of this organization.';
      end if;
    end if;
    return old;
  end if;

  -- UPDATE: block anything that turns the last active owner into a non-owner
  -- or a suspended member (covers demotion and suspension in one test).
  if TG_OP = 'UPDATE'
     and old.role = 'owner' and old.status = 'active'
     and not (new.role = 'owner' and new.status = 'active') then
    select count(*) into active_owners
    from public.organization_members
    where org_id = old.org_id and role = 'owner' and status = 'active';
    if active_owners <= 1 then
      raise exception 'Cannot demote or suspend the last active owner of this organization.';
    end if;
  end if;
  return new;
end;
$$;

-- ---------------------------------------------------------------------------
-- Suspend/remove cascades to the principal's worker tokens (revoke), so a
-- suspended person keeps no live router token.
-- ---------------------------------------------------------------------------
create or replace function public.cascade_membership_to_tokens()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  if TG_OP = 'DELETE'
     or (TG_OP = 'UPDATE' and new.status = 'suspended' and old.status <> 'suspended') then
    update public.workers
    set status = 'revoked'
    where principal_id = old.principal_id
      and org_id = old.org_id
      and status = 'active';
  end if;
  if TG_OP = 'DELETE' then
    return old;
  end if;
  return new;
end;
$$;

create trigger organization_members_cascade_tokens
  after update or delete on public.organization_members
  for each row execute function public.cascade_membership_to_tokens();
