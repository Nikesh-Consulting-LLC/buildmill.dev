-- 078_project_build_config: write-only build/test config for coding runs
-- (US-7.9).
--
-- Only the NAMES live here — readable by org members under RLS so the UI can
-- list them via the SDK. The VALUES are write-only secrets in the private
-- `data` bucket at <org_id>/projects/<project_id>/build-config/<NAME>, written
-- and read exclusively by `api` (service role) — no storage.objects policy, so
-- RLS default-deny blocks every browser (us-1.28 / us-1.37 pattern). This is a
-- deliberate, documented narrowing of the Phase-3 no-creds-on-workers rule:
-- code runs of the owning project receive these sandbox/test values so an
-- agent can actually build and verify. Never production secrets.

create table public.project_build_config (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  project_id uuid not null,
  name text not null check (name ~ '^[A-Za-z_][A-Za-z0-9_]*$'),
  updated_by uuid,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (project_id, name),
  foreign key (project_id, org_id)
    references public.projects (id, org_id) on delete cascade
);

create index project_build_config_org_idx on public.project_build_config (org_id);
create index project_build_config_project_idx
  on public.project_build_config (project_id);

alter table public.project_build_config enable row level security;

-- Names only — no insert/update/delete policy; the api writes via service role.
create policy "members read their org build config names"
  on public.project_build_config for select
  using (public.is_org_member(org_id));

create trigger project_build_config_touch
  before update on public.project_build_config
  for each row execute function public.touch_updated_at();
