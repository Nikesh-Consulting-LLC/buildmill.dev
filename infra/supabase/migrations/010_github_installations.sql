-- 010_github_installations: per-org GitHub App installations (US-1.19).
-- One platform-level GitHub App (credentials in apps/api/.env, never
-- here); this table stores only the installation id per org — no token,
-- no private key material.

create table public.github_installations (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  installation_id bigint not null unique,
  account_login text not null,
  account_type text not null check (account_type in ('User', 'Organization')),
  connected_by uuid not null references auth.users(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index github_installations_org_idx on public.github_installations (org_id);

alter table public.github_installations enable row level security;

create policy "members manage their org github installations"
  on public.github_installations for all
  using (public.is_org_member(org_id))
  with check (public.is_org_member(org_id));

create trigger github_installations_updated_at
  before update on public.github_installations
  for each row execute function public.touch_updated_at();
