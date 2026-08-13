-- 086_unified_principals: agents are users (US-9.1).
--
-- Phase 9 foundation. Humans and agents become one kind of thing — a
-- `principal` that is a member of an org with a role. The only difference is
-- authentication: a human logs in with email + password (GoTrue), an agent /
-- worker logs in with a token and has no email. This merges the two identity
-- worlds (`profiles` for people, `workers` for agents) into a single member
-- concept so everything downstream — roles, capabilities, assignment, review,
-- activity — treats a person and an agent uniformly.
--
-- Built so the runtime worker-token auth path is preserved byte-for-byte
-- (US-3.8 git proxy, US-3.2 claim, US-3.11 MCP keep working): agents simply
-- *also* gain a principal row + a membership. `get_worker_by_token`
-- (token_hash -> status='active' -> org_id) is untouched; `workers.principal_id`
-- is additive.
--
-- The RLS helpers `is_org_member(org)` / `is_org_owner(org)` /
-- `is_platform_admin()` keep their SIGNATURES so the ~52 policies that call
-- them keep working without edits — only their bodies change to resolve
-- auth.uid() -> its human principal -> membership. Four policies that inline
-- `organization_members.user_id` are rewritten through principals. `user_id`
-- is kept (nullable) and dual-written for humans so the 015 profile-embed FK
-- and any residual readers stay valid; `principal_id` is the new canonical key.

-- ---------------------------------------------------------------------------
-- A. principals
-- ---------------------------------------------------------------------------
create table public.principals (
  id uuid primary key default gen_random_uuid(),
  kind text not null check (kind in ('human', 'agent')),
  email text,                                                   -- humans have it; agents null
  display_name text,
  avatar_url text,
  auth_user_id uuid references auth.users(id) on delete cascade, -- humans; null for agents
  created_at timestamptz not null default now()
);

-- one human principal per auth user
create unique index principals_auth_user_id_key
  on public.principals (auth_user_id) where auth_user_id is not null;

alter table public.principals enable row level security;

-- Backfill: every existing profile becomes a human principal.
insert into public.principals (kind, email, display_name, avatar_url, auth_user_id)
select 'human', p.email, p.display_name, p.avatar_url, p.id
from public.profiles p;

-- ---------------------------------------------------------------------------
-- B. workers become "a principal's tokens"
-- ---------------------------------------------------------------------------
alter table public.workers
  add column principal_id uuid references public.principals(id) on delete cascade;

create index workers_principal_idx on public.workers (principal_id);

-- Human-typed workers link to that person's human principal.
update public.workers w
set principal_id = pr.id
from public.principals pr
where w.user_id is not null and pr.auth_user_id = w.user_id;

-- Autonomous workers (user_id is null) each get their own agent principal.
do $$
declare
  r record;
  v_pid uuid;
begin
  for r in select id, name from public.workers where principal_id is null loop
    insert into public.principals (kind, display_name)
    values ('agent', r.name)
    returning id into v_pid;
    update public.workers set principal_id = v_pid where id = r.id;
  end loop;
end $$;

-- ---------------------------------------------------------------------------
-- C. organization_members repointed to principals (+ status)
-- ---------------------------------------------------------------------------
alter table public.organization_members
  add column principal_id uuid references public.principals(id) on delete cascade;

alter table public.organization_members
  add column status text not null default 'active'
  check (status in ('active', 'suspended'));

-- Backfill existing (all-human) membership rows.
update public.organization_members m
set principal_id = pr.id
from public.principals pr
where pr.auth_user_id = m.user_id;

-- Swap the primary key from (org_id, user_id) to (org_id, principal_id).
-- The old PK must be dropped before user_id can go nullable (a PK column is
-- implicitly NOT NULL); agent membership rows then insert with a null user_id.
alter table public.organization_members drop constraint if exists organization_members_pkey;
alter table public.organization_members alter column user_id drop not null;

-- Agents become members: each agent principal joins its worker's org.
-- Role stays the current base 'member' here; the six-role set + member->
-- developer rename is US-9.2. user_id stays null for agent rows.
insert into public.organization_members (org_id, principal_id, role)
select w.org_id, w.principal_id, 'member'
from public.workers w
join public.principals pr on pr.id = w.principal_id and pr.kind = 'agent'
on conflict do nothing;

alter table public.organization_members alter column principal_id set not null;
alter table public.organization_members
  add constraint organization_members_pkey primary key (org_id, principal_id);

-- ---------------------------------------------------------------------------
-- D. helpers redefined over principals — SIGNATURES UNCHANGED
-- ---------------------------------------------------------------------------
create or replace function public.is_org_member(org uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1
    from public.organization_members m
    join public.principals pr on pr.id = m.principal_id
    where m.org_id = org
      and pr.auth_user_id = (select auth.uid())
  );
$$;

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
    join public.principals pr on pr.id = m.principal_id
    where m.org_id = org
      and pr.auth_user_id = (select auth.uid())
      and m.role = 'owner'
  );
$$;

create or replace function public.is_platform_admin()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1
    from public.organization_members m
    join public.principals pr on pr.id = m.principal_id
    join public.organizations o on o.id = m.org_id
    where pr.auth_user_id = (select auth.uid())
      and o.is_platform_admin = true
  );
$$;

-- ---------------------------------------------------------------------------
-- E. functions that write organization_members must supply principal_id
-- ---------------------------------------------------------------------------
-- Signup provisioning: profile (compat) + human principal + default org +
-- owner membership. user_id is dual-written for humans.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  new_org uuid;
  new_principal uuid;
begin
  insert into public.profiles (id, email, display_name)
  values (new.id, new.email, split_part(new.email, '@', 1));

  insert into public.principals (kind, email, display_name, auth_user_id)
  values ('human', new.email, split_part(new.email, '@', 1), new.id)
  returning id into new_principal;

  insert into public.organizations (name)
  values (initcap(split_part(new.email, '@', 1)) || '''s Workspace')
  returning id into new_org;

  insert into public.organization_members (org_id, principal_id, user_id, role)
  values (new_org, new_principal, new.id, 'owner');

  return new;
end;
$$;

-- Add-existing-member by email now resolves (or lazily creates) the human
-- principal and writes principal_id. Still owner-gated in US-9.1; US-9.2
-- generalizes to the manage_members capability.
create or replace function public.add_org_member_by_email(p_org uuid, p_email text)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user_id uuid;
  v_principal uuid;
begin
  if not public.is_org_owner(p_org) then
    raise exception 'not authorized';
  end if;

  select id into v_user_id from auth.users where lower(email) = lower(p_email) limit 1;
  if v_user_id is null then
    raise exception 'No account found for that email — ask them to sign up first, then add them again.';
  end if;

  select id into v_principal from public.principals where auth_user_id = v_user_id;
  if v_principal is null then
    insert into public.principals (kind, email, display_name, auth_user_id)
    select 'human', u.email, split_part(u.email, '@', 1), u.id
    from auth.users u where u.id = v_user_id
    returning id into v_principal;
  end if;

  if exists (
    select 1 from public.organization_members
    where org_id = p_org and principal_id = v_principal
  ) then
    raise exception 'That user is already a member of this organization.';
  end if;

  insert into public.organization_members (org_id, principal_id, user_id, role)
  values (p_org, v_principal, v_user_id, 'member');
end;
$$;

-- guard_last_owner() is unchanged: it keys off org_id + role only (no user_id),
-- so it keeps guaranteeing >= 1 owner per org after the repoint.

-- Security-definer visibility helpers. Because the "fellow principals" and
-- "teammates' profiles" policies would otherwise reference the principals table
-- from within a principals policy (infinite-recursion trap), the shared-org
-- test is resolved inside a definer function that bypasses RLS.
create or replace function public.shares_org_with_caller(p_principal uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1
    from public.organization_members a
    join public.principals pa on pa.id = a.principal_id
    join public.organization_members b on b.org_id = a.org_id
    where pa.auth_user_id = (select auth.uid())
      and b.principal_id = p_principal
  );
$$;

create or replace function public.shares_org_with_caller_user(p_user uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1
    from public.organization_members a
    join public.principals pa on pa.id = a.principal_id
    join public.organization_members b on b.org_id = a.org_id
    join public.principals pb on pb.id = b.principal_id
    where pa.auth_user_id = (select auth.uid())
      and pb.auth_user_id = p_user
  );
$$;

revoke execute on function public.shares_org_with_caller(uuid) from public, anon;
grant execute on function public.shares_org_with_caller(uuid) to authenticated;
revoke execute on function public.shares_org_with_caller_user(uuid) from public, anon;
grant execute on function public.shares_org_with_caller_user(uuid) to authenticated;

-- ---------------------------------------------------------------------------
-- G. policies: rewrite the four that inline organization_members.user_id, and
--    add principal-visibility policies. (All other policies route through the
--    helpers above and need no change.)
-- ---------------------------------------------------------------------------

-- organization_members: a caller sees their own membership rows.
drop policy if exists "users can view their own memberships" on public.organization_members;
create policy "users can view their own memberships"
  on public.organization_members for select
  using (
    principal_id in (
      select id from public.principals where auth_user_id = (select auth.uid())
    )
  );

-- organizations: owner-update via the (now principal-based) helper.
drop policy if exists "owners can update their orgs" on public.organizations;
create policy "owners can update their orgs"
  on public.organizations for update
  using (public.is_org_owner(id));

-- profiles: teammates' profiles visible through shared orgs (definer helper).
drop policy if exists "org members can view teammates' profiles" on public.profiles;
create policy "org members can view teammates' profiles"
  on public.profiles for select
  using (
    id = (select auth.uid())
    or public.shares_org_with_caller_user(id)
  );

-- principals: see your own, and see fellow principals (human or agent) in orgs
-- you belong to (drives the roster + PostgREST embeds off organization_members).
create policy "members can view fellow principals"
  on public.principals for select
  using (
    auth_user_id = (select auth.uid())
    or public.shares_org_with_caller(id)
  );

create policy "principals can update themselves"
  on public.principals for update
  using (auth_user_id = (select auth.uid()))
  with check (auth_user_id = (select auth.uid()));
