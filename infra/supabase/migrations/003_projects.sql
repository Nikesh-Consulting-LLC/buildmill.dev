-- 003_projects: project entity (US-1.5). A project links the factory to a
-- GitHub repository. Org-scoped + RLS like everything else.

create table public.projects (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  name text not null,
  description text,
  repo_full_name text not null check (repo_full_name ~ '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$'),
  default_branch text not null default 'main',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index projects_org_idx on public.projects (org_id);

alter table public.projects enable row level security;

create policy "members manage their org projects"
  on public.projects for all
  using (public.is_org_member(org_id))
  with check (public.is_org_member(org_id));

create trigger projects_touch
  before update on public.projects
  for each row execute function public.touch_updated_at();
