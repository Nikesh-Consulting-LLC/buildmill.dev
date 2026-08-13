-- 020_deployments: project deployments (US-1.31). Org-scoped, RLS via
-- is_org_member(). A deployment is a reusable definition of where and
-- how a project ships: target server, GitHub branch, target folder on
-- the server, a shell script executed there after transfer, and a
-- per-deployment run timeout. Configuration only — runs are US-1.32.
--
-- Cross-org integrity: FK validation bypasses RLS, so plain FKs would
-- let an attacker reference another org's server/project ids from rows
-- in their own org. Composite FKs on (id, org_id) make that impossible:
-- the referenced row must carry the same org_id as the deployment.

alter table public.servers
  add constraint servers_id_org_unique unique (id, org_id);

alter table public.projects
  add constraint projects_id_org_unique unique (id, org_id);

create table public.deployments (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  project_id uuid not null,
  server_id uuid not null,
  name text not null,
  branch text not null,
  target_folder text not null check (target_folder like '/%'),
  script text not null default '',
  run_timeout_minutes int not null default 30
    check (run_timeout_minutes between 1 and 720),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (project_id, name),
  -- deleting a project takes its deployments with it; a server with
  -- deployments pointing at it refuses to die (US-1.31: error names them)
  foreign key (project_id, org_id)
    references public.projects (id, org_id) on delete cascade,
  foreign key (server_id, org_id)
    references public.servers (id, org_id) on delete restrict
);

create index deployments_org_idx on public.deployments (org_id);
create index deployments_project_idx on public.deployments (project_id);
create index deployments_server_idx on public.deployments (server_id);

alter table public.deployments enable row level security;

create policy "members manage their org deployments"
  on public.deployments for all
  using (public.is_org_member(org_id))
  with check (public.is_org_member(org_id));

create trigger deployments_touch
  before update on public.deployments
  for each row execute function public.touch_updated_at();
