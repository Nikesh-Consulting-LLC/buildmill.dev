-- 243_project_modules: modules name what a release touched (US-82.3).
--
-- A module is a manager-named area of the codebase: a label plus the path
-- globs that belong to it. Cases can be tagged with one. At release cut the
-- factory matches the release's changed files against the globs and records
-- the touched module names on the release — a suggestion engine for manual
-- regression cases, never a gate and never a selector for automated suites
-- (those always run whole).

create table public.project_modules (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  project_id uuid not null,
  name text not null,
  -- pathspec-style globs, e.g. ["apps/api/**", "infra/supabase/**"]
  path_globs jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (project_id, name),
  unique (id, org_id),
  foreign key (project_id, org_id)
    references public.projects (id, org_id) on delete cascade
);

create index project_modules_org_idx on public.project_modules (org_id);
create index project_modules_project_idx on public.project_modules (project_id);

alter table public.project_modules enable row level security;

create policy "members manage their org project modules"
  on public.project_modules for all
  using (public.is_org_member(org_id))
  with check (public.is_org_member(org_id));

create trigger project_modules_touch
  before update on public.project_modules
  for each row execute function public.touch_updated_at();

-- set null (module_id) only — a bare SET NULL would null org_id, which is NOT NULL.
alter table public.test_cases
  add column module_id uuid;
alter table public.test_cases
  add constraint test_cases_module_fk foreign key (module_id, org_id)
  references public.project_modules (id, org_id) on delete set null (module_id);

-- The touched module names, computed once at cut from the release's commit
-- range. Names, not ids: the snapshot must stay readable even if a module is
-- later renamed or deleted, the same reasoning as included_items.
alter table public.releases
  add column touched_modules jsonb not null default '[]'::jsonb;
