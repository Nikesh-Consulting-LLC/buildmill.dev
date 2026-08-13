-- 098_git_power_grants: Power Git access (US-9.19).
--
-- Lets an Admin grant a specific principal, on a specific project, the right to
-- push through the factory git remote (US-3.8) WITHOUT a claimed work item —
-- the traditional clone/branch/push/PR flow. The pushed branch lands on GitHub
-- only: no synthetic run, no auto-PR (the "escape hatch to GitHub" trade).
--
-- A grant is per-(principal, project) and UNRESTRICTED by default; four rails
-- tighten it. This is the runtime-path enforcement US-9.2 deferred: it sits at
-- the git proxy's receive-pack, resolving workers.principal_id -> the grant.

-- ---------------------------------------------------------------------------
-- A. git_power_grants — the per-(principal, project) grant + rails
-- ---------------------------------------------------------------------------
create table public.git_power_grants (
  org_id uuid not null references public.organizations(id) on delete cascade,
  project_id uuid not null,
  principal_id uuid not null references public.principals(id) on delete cascade,
  -- rails: all default true == fully unrestricted. Flip to false to tighten.
  allow_default_branch boolean not null default true,  -- direct push to default_branch
  allow_force_push boolean not null default true,      -- history rewrite
  allow_branch_delete boolean not null default true,   -- zero-SHA update
  allow_tag_push boolean not null default true,        -- non-refs/heads/ refs
  granted_by uuid references public.principals(id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (project_id, principal_id),
  -- composite-org FK so a grant can't reference another org's project (which
  -- would otherwise pass this table's own org-scoped RLS). Mirrors 043.
  foreign key (project_id, org_id)
    references public.projects (id, org_id) on delete cascade
);

create index git_power_grants_org_idx on public.git_power_grants (org_id);
create index git_power_grants_principal_idx on public.git_power_grants (principal_id);

alter table public.git_power_grants enable row level security;

-- Read: any org member (so the UI can show who has power access).
create policy "members read their org git power grants"
  on public.git_power_grants for select
  using (public.is_org_member(org_id));

-- Write: manage_project (owner/admin) — the same capability that governs repos,
-- build config, secrets, deployments (US-9.2).
create policy "project managers insert git power grants"
  on public.git_power_grants for insert
  with check (public.has_org_capability(org_id, 'manage_project'));
create policy "project managers update git power grants"
  on public.git_power_grants for update
  using (public.has_org_capability(org_id, 'manage_project'))
  with check (public.has_org_capability(org_id, 'manage_project'));
create policy "project managers delete git power grants"
  on public.git_power_grants for delete
  using (public.has_org_capability(org_id, 'manage_project'));

-- keep updated_at honest
create or replace function public.touch_git_power_grant()
returns trigger
language plpgsql
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

create trigger git_power_grants_touch
  before update on public.git_power_grants
  for each row execute function public.touch_git_power_grant();

-- ---------------------------------------------------------------------------
-- B. git_power_grant_events — append-only audit (043 pattern)
-- ---------------------------------------------------------------------------
create table public.git_power_grant_events (
  id bigint generated always as identity primary key,
  org_id uuid not null references public.organizations(id) on delete cascade,
  project_id uuid not null,
  principal_id uuid not null,
  actor text not null default '',
  event text not null check (event in ('granted', 'updated', 'revoked')),
  detail jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index git_power_grant_events_org_idx
  on public.git_power_grant_events (org_id, id desc);

alter table public.git_power_grant_events enable row level security;

create policy "members read their org git power grant events"
  on public.git_power_grant_events for select
  using (public.is_org_member(org_id));

create or replace function public.log_git_power_grant_change()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_row record;
  v_event text;
  v_actor text := coalesce(nullif(auth.jwt() ->> 'email', ''), 'api');
begin
  if tg_op = 'DELETE' then
    v_row := old; v_event := 'revoked';
  elsif tg_op = 'INSERT' then
    v_row := new; v_event := 'granted';
  else
    v_row := new; v_event := 'updated';
  end if;
  insert into public.git_power_grant_events
    (org_id, project_id, principal_id, actor, event, detail)
  values (
    v_row.org_id, v_row.project_id, v_row.principal_id, v_actor, v_event,
    jsonb_build_object(
      'allow_default_branch', v_row.allow_default_branch,
      'allow_force_push', v_row.allow_force_push,
      'allow_branch_delete', v_row.allow_branch_delete,
      'allow_tag_push', v_row.allow_tag_push
    )
  );
  return null;
end;
$$;

create trigger git_power_grants_audit
  after insert or update or delete on public.git_power_grants
  for each row execute function public.log_git_power_grant_change();

-- ---------------------------------------------------------------------------
-- C. git_power_branch_heads — recorded head per power-pushed branch.
--
-- The proxy has no object graph, so it can't detect a non-fast-forward on its
-- own (see gitproxy.py's module docstring). For the allow_force_push rail we
-- record each accepted power push's head — exactly like a run records
-- pushed_head_sha — so the next push can be rewrite-checked (old != recorded).
-- Written ONLY by the service-role API path: RLS is enabled with a read policy
-- and no write policy, so the client default-deny blocks all writes.
-- ---------------------------------------------------------------------------
create table public.git_power_branch_heads (
  project_id uuid not null references public.projects(id) on delete cascade,
  principal_id uuid not null references public.principals(id) on delete cascade,
  branch text not null,
  head_sha text not null,
  updated_at timestamptz not null default now(),
  primary key (project_id, principal_id, branch)
);

alter table public.git_power_branch_heads enable row level security;

create policy "members read their project power branch heads"
  on public.git_power_branch_heads for select
  using (public.is_org_member(
    (select org_id from public.projects p where p.id = project_id)
  ));
