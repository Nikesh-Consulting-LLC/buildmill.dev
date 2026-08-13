-- US-57.3 follow-on (2026-07-31): a pool placement used to fail outright
-- with "This machine already has a job running" whenever the host's
-- one-job-per-host lock was already held — a real dead end for the wizard,
-- surfaced live the moment Pod-001 got busy. This table lets the API
-- acknowledge the placement immediately and drain it once the host frees,
-- instead of making the tenant retry by hand.

create table public.agent_pool_placement_requests (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  pool_id uuid not null references public.agent_servers(id) on delete cascade,
  worker_id uuid not null unique references public.workers(id) on delete cascade,
  status text not null default 'pending' check (status in ('pending', 'failed')),
  error text,
  requested_by uuid,
  requested_by_email text,
  created_at timestamptz not null default now()
);

create index agent_pool_placement_requests_pool_idx
  on public.agent_pool_placement_requests (pool_id);

alter table public.agent_pool_placement_requests enable row level security;

-- Read-only for the org that owns the worker (so the wizard/roster can show
-- "queued" vs "failed"); all writes are the API's own (service role), same
-- as agent_server_jobs' job log — a tenant never inserts or edits its own
-- placement request.
create policy agent_pool_placement_requests_select
  on public.agent_pool_placement_requests for select
  using (public.is_org_member(org_id));

alter publication supabase_realtime add table public.agent_pool_placement_requests;
