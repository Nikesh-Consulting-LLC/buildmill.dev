-- 022_deployment_env_vars: per-deployment environment variables (US-1.37).
--
-- Only the NAMES live here — visible to org members under RLS so the UI
-- can list them via the SDK. The VALUES are write-only secrets in the
-- private `data` bucket (<org_id>/deployments/<deployment_id>/env/<NAME>),
-- written and read exclusively by `api` (service role), per the us-1.28
-- bucket rules. Rows are written by `api` too (service role), atomically
-- with the bucket object — clients get SELECT and nothing else.

create table public.deployment_env_vars (
  org_id uuid not null references public.organizations(id) on delete cascade,
  deployment_id uuid not null,
  name text not null check (name ~ '^[A-Za-z_][A-Za-z0-9_]*$'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (deployment_id, name),
  foreign key (deployment_id, org_id)
    references public.deployments (id, org_id) on delete cascade
);

create index deployment_env_vars_org_idx on public.deployment_env_vars (org_id);

alter table public.deployment_env_vars enable row level security;

create policy "members read their org deployment env var names"
  on public.deployment_env_vars for select
  using (public.is_org_member(org_id));

create trigger deployment_env_vars_touch
  before update on public.deployment_env_vars
  for each row execute function public.touch_updated_at();
