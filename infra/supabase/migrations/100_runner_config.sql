-- 100_runner_config: server-side supervisor-runner configuration (US-10.2).
--
-- Once a runner connects (US-10.1), everything about how it behaves lives here,
-- on the server: which agent modules it may use, which model routes the brain
-- and each run kind take, its concurrency, and its autonomy policy. The config
-- is delivered on `runner.hello` and pushed live over the socket (`config.update`)
-- when it changes. The browser READS config (org-scoped); WRITES go through the
-- API's capability-gated PATCH endpoint (service role) so the live push happens.

create table public.runner_config (
  worker_id uuid primary key references public.workers(id) on delete cascade,
  org_id uuid not null references public.organizations(id) on delete cascade,
  enabled_modules text[] not null default '{}',
  model_routes jsonb not null default '{}'::jsonb,
  concurrency int not null default 1 check (concurrency between 1 and 16),
  autonomy_policy jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

alter table public.runner_config enable row level security;

-- Org members read their runners' config (feeds the management UI, US-10.9).
-- No client write policy: config is written only by the API service role after
-- a manage_work capability check, so the live config.update push can fire.
create policy "org members read runner config"
  on public.runner_config for select
  using (public.is_org_member(org_id));

-- Live edits reflected in the management UI.
alter publication supabase_realtime add table public.runner_config;
