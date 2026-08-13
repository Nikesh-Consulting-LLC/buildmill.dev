-- 087_role_capability_layer: six roles + data-driven capabilities (US-9.2).
--
-- On the unified-principals foundation (086), membership graduates from a
-- two-value role (owner|member) to a six-role model — owner, admin, lead,
-- developer, reviewer, viewer — applied to BOTH humans and agents.
-- Authorization is expressed as capabilities, not hard-coded role names:
-- `role_capabilities` holds the role->capability grid (one global default,
-- Superadmin-editable in US-9.3), and `has_org_capability(org, capability)`
-- resolves a caller's role in an org to a yes/no. RLS gates on capabilities so
-- the matrix can change without touching policies.

-- ---------------------------------------------------------------------------
-- Role set expansion + member -> developer migration (incl. agent principals)
-- ---------------------------------------------------------------------------
alter table public.organization_members drop constraint if exists organization_members_role_check;

update public.organization_members set role = 'developer' where role = 'member';

alter table public.organization_members
  add constraint organization_members_role_check
  check (role in ('owner', 'admin', 'lead', 'developer', 'reviewer', 'viewer'));

alter table public.organization_members alter column role set default 'developer';

-- ---------------------------------------------------------------------------
-- role_capabilities: global default matrix (US-9.3 editor writes it via
-- service role). Readable by any authenticated user; no client write policy.
-- ---------------------------------------------------------------------------
create table public.role_capabilities (
  role text not null
    check (role in ('owner', 'admin', 'lead', 'developer', 'reviewer', 'viewer')),
  capability text not null
    check (capability in ('manage_org', 'manage_members', 'manage_project',
                          'manage_work', 'review_work', 'develop', 'view')),
  allowed boolean not null default false,
  primary key (role, capability)
);

alter table public.role_capabilities enable row level security;

create policy "authenticated can read role_capabilities"
  on public.role_capabilities for select
  to authenticated
  using (true);

-- Seed the full 6 x 7 grid (explicit true/false so the US-9.3 editor toggles
-- existing rows). Matrix per the story:
--   manage_org     : owner
--   manage_members : owner, admin
--   manage_project : owner, admin
--   manage_work    : owner, admin, lead
--   review_work    : owner, admin, lead, reviewer
--   develop        : owner, admin, lead, developer
--   view           : everyone
insert into public.role_capabilities (role, capability, allowed) values
  ('owner','manage_org',true),('owner','manage_members',true),('owner','manage_project',true),
  ('owner','manage_work',true),('owner','review_work',true),('owner','develop',true),('owner','view',true),
  ('admin','manage_org',false),('admin','manage_members',true),('admin','manage_project',true),
  ('admin','manage_work',true),('admin','review_work',true),('admin','develop',true),('admin','view',true),
  ('lead','manage_org',false),('lead','manage_members',false),('lead','manage_project',false),
  ('lead','manage_work',true),('lead','review_work',true),('lead','develop',true),('lead','view',true),
  ('developer','manage_org',false),('developer','manage_members',false),('developer','manage_project',false),
  ('developer','manage_work',false),('developer','review_work',false),('developer','develop',true),('developer','view',true),
  ('reviewer','manage_org',false),('reviewer','manage_members',false),('reviewer','manage_project',false),
  ('reviewer','manage_work',false),('reviewer','review_work',true),('reviewer','develop',false),('reviewer','view',true),
  ('viewer','manage_org',false),('viewer','manage_members',false),('viewer','manage_project',false),
  ('viewer','manage_work',false),('viewer','review_work',false),('viewer','develop',false),('viewer','view',true);

-- ---------------------------------------------------------------------------
-- has_org_capability: caller's role in an org grants the capability, or
-- Superadmin. Resolves through the 086 principal keying. False for non-members.
-- ---------------------------------------------------------------------------
create or replace function public.has_org_capability(p_org uuid, p_capability text)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select public.is_platform_admin() or exists (
    select 1
    from public.organization_members m
    join public.principals pr on pr.id = m.principal_id
    join public.role_capabilities rc
      on rc.role = m.role and rc.capability = p_capability
    where m.org_id = p_org
      and pr.auth_user_id = (select auth.uid())
      and rc.allowed = true
  );
$$;

revoke execute on function public.has_org_capability(uuid, text) from public, anon;
grant execute on function public.has_org_capability(uuid, text) to authenticated;

-- ---------------------------------------------------------------------------
-- Generalize the is_org_owner call sites to capabilities (audited: exactly the
-- organization_members / organizations / deployments write policies and
-- add_org_member_by_email). Member management -> manage_members (owner+admin);
-- org rename/settings -> manage_org (owner); protected deployments ->
-- manage_project (owner+admin).
-- ---------------------------------------------------------------------------
drop policy if exists "owners can update their org's membership rows" on public.organization_members;
create policy "managers can update their org's membership rows"
  on public.organization_members for update
  using (public.has_org_capability(org_id, 'manage_members'))
  with check (public.has_org_capability(org_id, 'manage_members'));

drop policy if exists "owners can remove their org's membership rows" on public.organization_members;
create policy "managers can remove their org's membership rows"
  on public.organization_members for delete
  using (public.has_org_capability(org_id, 'manage_members'));

drop policy if exists "owners can update their orgs" on public.organizations;
create policy "org managers can update their orgs"
  on public.organizations for update
  using (public.has_org_capability(id, 'manage_org'));

drop policy if exists "members create deployments (protected needs owner)" on public.deployments;
create policy "members create deployments (protected needs manage_project)"
  on public.deployments for insert
  with check (public.is_org_member(org_id)
    and ((not protected) or public.has_org_capability(org_id, 'manage_project')));

drop policy if exists "members update unprotected deployments" on public.deployments;
create policy "members update deployments (protected needs manage_project)"
  on public.deployments for update
  using (public.is_org_member(org_id)
    and ((not protected) or public.has_org_capability(org_id, 'manage_project')))
  with check (public.is_org_member(org_id)
    and ((not protected) or public.has_org_capability(org_id, 'manage_project')));

drop policy if exists "members delete unprotected deployments" on public.deployments;
create policy "members delete deployments (protected needs manage_project)"
  on public.deployments for delete
  using (public.is_org_member(org_id)
    and ((not protected) or public.has_org_capability(org_id, 'manage_project')));

-- add_org_member_by_email: gate on manage_members (was is_org_owner).
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
  if not public.has_org_capability(p_org, 'manage_members') then
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
  values (p_org, v_principal, v_user_id, 'developer');
end;
$$;

-- ---------------------------------------------------------------------------
-- Project-settings writes -> manage_project (split the ALL policy so reads
-- stay open to every member; writes require the capability).
-- ---------------------------------------------------------------------------
drop policy if exists "members manage their org projects" on public.projects;
create policy "members view their org projects"
  on public.projects for select
  using (public.is_org_member(org_id));
create policy "managers insert their org projects"
  on public.projects for insert
  with check (public.has_org_capability(org_id, 'manage_project'));
create policy "managers update their org projects"
  on public.projects for update
  using (public.has_org_capability(org_id, 'manage_project'))
  with check (public.has_org_capability(org_id, 'manage_project'));
create policy "managers delete their org projects"
  on public.projects for delete
  using (public.has_org_capability(org_id, 'manage_project'));

-- Deferred (owned by later stories, kept on is_org_member here so nothing
-- regresses): issues/epics/runs (manage_work, US-9.9), servers &
-- project_guidelines (manage_project — folded in when those surfaces are
-- reworked), workers/worker_capabilities (develop, US-9.8 — confirmed the
-- developer role that runs autonomous workers keeps is_org_member access with
-- no regression). Read paths everywhere stay on is_org_member.
