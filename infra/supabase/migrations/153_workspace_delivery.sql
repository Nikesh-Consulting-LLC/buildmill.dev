-- 153_workspace_delivery: the factory remembers what code an agent already
-- has (US-31.6).
--
-- get_workspace hands over the whole tree as a zip, every call. That is right
-- for a throwaway checkout and wrong for a workspace an agent keeps
-- (US-31.8), for two reasons:
--
--   * it cannot update anything — full tree or nothing, so a second run on
--     the same project re-downloads a tree it already has;
--   * extracting it over an older tree is silently WRONG — a file deleted
--     upstream is not in the new zip, so nothing removes it. It stays on disk
--     and keeps compiling.
--
-- The fix is not to make the agent keep bookkeeping. The server records what
-- it last served, per (worker, project), and answers either a full tree or a
-- delta with EXPLICIT deletions. State on the server survives an agent that
-- crashed mid-extract, a machine rebuilt from scratch, and an agent that
-- lies — none of which a client-side manifest survives.

create table public.workspace_deliveries (
  worker_id uuid not null references public.workers(id) on delete cascade,
  project_id uuid not null,
  org_id uuid not null references public.organizations(id) on delete cascade,

  -- the commit whose tree the agent is believed to hold
  base_sha text not null,
  -- every path served at that sha; the delete list on the next delta is
  -- computed against it, so a file that vanishes upstream is named rather
  -- than left behind
  paths text[] not null default '{}',

  served_at timestamptz not null default now(),

  primary key (worker_id, project_id),
  foreign key (project_id, org_id)
    references public.projects (id, org_id) on delete cascade
);

create index workspace_deliveries_org_idx on public.workspace_deliveries (org_id);

alter table public.workspace_deliveries enable row level security;

-- Org members read (it explains what an agent is holding); only `api` writes,
-- with the service role, like every other agent-facing bookkeeping table.
create policy "org members read workspace deliveries"
  on public.workspace_deliveries for select
  using (public.is_org_member(org_id));

comment on table public.workspace_deliveries is
  'US-31.6: per (worker, project), the sha and path manifest the factory last '
  'served. Lets get_workspace answer a delta with explicit deletions instead '
  'of a whole tree, and makes a kept workspace (US-31.8) correct without a '
  '.git. Written only after a response is successfully produced — a delivery '
  'the agent never received must not be recorded as one.';

-- US-31.8: what the kept workspaces cost -----------------------------------
-- Persistence is the point of us-31.8, so its footprint has to be a number
-- the manager can see on the fleet page rather than a surprise when the disk
-- fills. Read by the existing US-26.7 probe; current values only, no series.

alter table public.agent_servers
  add column workspace_bytes bigint,
  add column workspace_count int;

comment on column public.agent_servers.workspace_bytes is
  'US-31.8: total bytes under the machine''s agent workspaces at the last '
  'probe. Kept per-project workspaces hold dependencies deliberately; this is '
  'what that costs.';
comment on column public.agent_servers.workspace_count is
  'US-31.8: how many per-project workspaces exist across this machine''s '
  'agents at the last probe.';
