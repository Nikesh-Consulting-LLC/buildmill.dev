-- 253_beta_gate: every new account waits for a superadmin nod (us-94.1).
--
-- The public site now invites anyone in; the factory runs on real capacity.
-- A profile gains approved_at / approved_by. Every account existing at apply
-- time is grandfathered in this same migration; a new signup (handle_new_user
-- leaves the columns null) authenticates but waits at the web app's /gate
-- until a platform admin approves it from SuperAdmin → Accounts → Users.
--
-- Enforcement is one choke point. is_approved_user() joins the three
-- membership helpers every RLS policy routes through and every FastAPI
-- capability check RPCs under the caller's own JWT (is_org_member /
-- is_org_owner / has_org_capability — the same trio 089 taught about member
-- status and 193 closed for suspended platform admins). Gating them gates
-- browser CRUD and API orchestration alike; nothing else needs to know the
-- state exists.
--
-- The three live bodies were verified byte-identical across prod and dev on
-- 2026-08-14 and their md5s are pinned below, per the drift rules
-- (172/174/176/185/187/193): if a live body is neither the pinned original
-- nor already gated, this raises and the whole migration rolls back rather
-- than silently clobbering something that moved.

-- 1. The state: null approved_at = waiting at the gate.
alter table public.profiles
  add column if not exists approved_at timestamptz,
  add column if not exists approved_by uuid;

comment on column public.profiles.approved_at is
  'us-94.1: when a platform admin approved this account; null = waiting at the beta gate';
comment on column public.profiles.approved_by is
  'auth.users id of the approving platform admin; deliberately no FK so deleting an admin never cascades into the approval record';

-- 2. Grandfather: nobody working today ever sees the gate (AC5).
update public.profiles set approved_at = now() where approved_at is null;

-- 3. Close the self-approval hole. profiles carries "users can update their
-- own profile" (RLS is row-level, not column-level), so without this a
-- pending user could set their own approved_at. The browser legitimately
-- updates exactly two columns (profile-form.tsx); the API goes through the
-- service role, which keeps its own grants.
revoke update on table public.profiles from authenticated, anon;
grant update (display_name, avatar_url) on table public.profiles to authenticated;

-- 4. The predicate. Null auth.uid() (anon) stays false, which changes
-- nothing — the helpers already returned false for anon callers.
create or replace function public.is_approved_user()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1
    from public.profiles p
    where p.id = (select auth.uid())
      and p.approved_at is not null
  );
$$;

-- 5. Assert the live bodies are where this migration expects them.
do $guard$
declare
  bad text := '';
  rec record;
begin
  for rec in
    select p.proname, p.prosrc, md5(p.prosrc) as body_md5
    from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public'
      and p.proname in ('is_org_member', 'is_org_owner', 'has_org_capability')
  loop
    if position('is_approved_user' in rec.prosrc) > 0 then
      raise notice '253 already applied to %; replacing with the same body', rec.proname;
    elsif rec.proname = 'is_org_member' and rec.body_md5 <> 'ce5196c95b72ece88da869f859a3de36' then
      bad := bad || ' ' || rec.proname;
    elsif rec.proname = 'is_org_owner' and rec.body_md5 <> 'dc1ba4a549b01fd7a55b71d9ec98e220' then
      bad := bad || ' ' || rec.proname;
    elsif rec.proname = 'has_org_capability' and rec.body_md5 <> '40ad9b3b71eef1e97122575a8541386a' then
      bad := bad || ' ' || rec.proname;
    end if;
  end loop;
  if bad <> '' then
    raise exception
      'live function bodies have drifted from what 253 expects (%) — '
      're-derive this migration from the live bodies instead of applying it', bad;
  end if;
end
$guard$;

-- 6. The gated bodies — the pinned originals with the approval predicate
-- added, nothing else moved.
create or replace function public.is_org_member(org uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select public.is_approved_user() and exists (
    select 1
    from public.organization_members m
    join public.principals pr on pr.id = m.principal_id
    where m.org_id = org
      and pr.auth_user_id = (select auth.uid())
      and m.status = 'active'
  );
$$;

create or replace function public.is_org_owner(org uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select public.is_approved_user() and exists (
    select 1
    from public.organization_members m
    join public.principals pr on pr.id = m.principal_id
    where m.org_id = org
      and pr.auth_user_id = (select auth.uid())
      and m.role = 'owner'
      and m.status = 'active'
  );
$$;

-- Approval comes before every capability — including the platform-admin
-- short-circuit. (Admins are grandfathered above, so this bites only a
-- hypothetical future pending admin, which is exactly right.)
create or replace function public.has_org_capability(p_org uuid, p_capability text)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select public.is_approved_user() and (public.is_platform_admin() or exists (
    select 1
    from public.organization_members m
    join public.principals pr on pr.id = m.principal_id
    join public.role_capabilities rc
      on rc.role = m.role and rc.capability = p_capability
    where m.org_id = p_org
      and pr.auth_user_id = (select auth.uid())
      and m.status = 'active'
      and rc.allowed = true
  ));
$$;
