-- 142_agent_servers: agent servers, their agent slots, and the job log of
-- every SSH-side action (Phase 26, US-26.1–26.10).
--
-- An agent server is a machine an admin registers so Build Mill can install,
-- run, update and retire coding agents on it. It layers on the EXISTING
-- servers registry (019_servers.sql) rather than opening a second place SSH
-- credentials live: agent_servers.server_id points at the servers row, and
-- the credential material stays exactly where it is — the private `data`
-- bucket, service-role only, no storage policy, ever.
--
-- The only secret that reaches an agent machine is one worker token per
-- slot, written by `api` over SFTP into a 0600 env file. Model keys never
-- go there (the LLM gateway mints short-lived scoped keys per run) and no
-- GitHub credential goes there either (the supervisor clones through the
-- factory git proxy with that same worker token).
--
-- RLS follows the runner_config pattern (100_runner_config.sql): org members
-- read, and there is NO client write policy — every write goes through `api`
-- with the service role after a manage_org capability check, so the SSH-side
-- work and the row can never disagree about who authorised it.

-- ---------------------------------------------------------------------------
-- agent_servers — one row per machine that runs agents
-- ---------------------------------------------------------------------------
create table public.agent_servers (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  server_id uuid not null references public.servers(id) on delete cascade,

  status text not null default 'new'
    check (status in ('new', 'provisioning', 'ready', 'degraded', 'error', 'removed')),

  -- install root; the documented layout lives under it (app/, venv/, env/, agents/)
  workdir text not null default '/opt/buildmill',

  -- what this machine is equipped with (US-26.3). Host declares what it CAN
  -- run; a slot's runner_config decides what it DOES run.
  modules text[] not null default '{}',
  extra_packages text[] not null default '{}',
  setup_commands text not null default '',
  cli_versions jsonb not null default '{}'::jsonb,

  -- off by default: an agent that can apt-get can also rewrite the machine
  -- that audits it (US-26.2).
  allow_agent_sudo boolean not null default false,

  -- runner config + capability grants a new slot starts with (US-26.6)
  slot_template jsonb not null default '{}'::jsonb,

  -- the hash IS the version: no version file to bump, so the recorded
  -- version and the installed code cannot disagree (US-26.2/26.8).
  bundle_hash text,
  agent_version text,
  provisioned_at timestamptz,

  -- probe readout (US-26.7). Current values only — no time series.
  os_release text,
  cpu_count int,
  mem_total_mb int,
  mem_free_mb int,
  disk_total_gb numeric(10, 2),
  disk_free_gb numeric(10, 2),
  load_avg numeric(6, 2),
  last_probe_at timestamptz,
  probe_error text,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  -- one agent config per machine: two configs on one host would race each
  -- other's systemd units.
  unique (server_id),
  -- composite target so children carry org_id and cannot be re-parented
  -- across orgs
  unique (id, org_id)
);

create index agent_servers_org_idx on public.agent_servers (org_id);

alter table public.agent_servers enable row level security;

create policy "org members read agent servers"
  on public.agent_servers for select
  using (public.is_org_member(org_id));

create trigger agent_servers_touch
  before update on public.agent_servers
  for each row execute function public.touch_updated_at();

-- ---------------------------------------------------------------------------
-- agent_slots — one agent: an identity, a service instance, a workspace
-- ---------------------------------------------------------------------------
-- A slot is a FULL identity, not a unit of concurrency: its principal and
-- worker are ordinary rows, so Team, the capability matrix, the runner
-- console, presence and pool claiming operate on them unchanged. An agent
-- installed this way is indistinguishable from one installed by hand.
create table public.agent_slots (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  agent_server_id uuid not null,

  slot_index int not null check (slot_index between 1 and 64),
  name text not null,

  -- kept on delete: past runs name the agent that did them, so removal
  -- deactivates the identity rather than deleting it (US-26.9).
  worker_id uuid references public.workers(id) on delete set null,
  principal_id uuid references public.principals(id) on delete set null,

  service_name text not null,          -- buildmill-agent@<index>
  workspace_path text not null,        -- RUNNER_WORKSPACE for this slot

  -- what the app wants the machine to be doing; the job reconciles to it
  desired_state text not null default 'paused'
    check (desired_state in ('paused', 'enabled', 'stopped')),
  -- what the probe last observed. Kept separate so the UI can show
  -- disagreement instead of asserting the row and being wrong (US-26.5).
  service_state text
    check (service_state is null or service_state in ('active', 'failed', 'inactive', 'unknown')),
  last_service_check timestamptz,

  agent_version text,

  status text not null default 'active' check (status in ('active', 'removed')),

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  unique (id, org_id),
  foreign key (agent_server_id, org_id)
    references public.agent_servers (id, org_id) on delete cascade
);

create index agent_slots_org_idx on public.agent_slots (org_id);
create index agent_slots_server_idx on public.agent_slots (agent_server_id);
create index agent_slots_worker_idx on public.agent_slots (worker_id);

-- indexes are unique among live slots; a removed slot frees its number
create unique index agent_slots_live_index_key
  on public.agent_slots (agent_server_id, slot_index) where status = 'active';

-- a worker may back at most one live slot (US-26.4's bind-an-existing-agent)
create unique index agent_slots_live_worker_key
  on public.agent_slots (worker_id) where status = 'active' and worker_id is not null;

alter table public.agent_slots enable row level security;

create policy "org members read agent slots"
  on public.agent_slots for select
  using (public.is_org_member(org_id));

create trigger agent_slots_touch
  before update on public.agent_slots
  for each row execute function public.touch_updated_at();

-- ---------------------------------------------------------------------------
-- agent_server_jobs — every SSH-side action, with a live log
-- ---------------------------------------------------------------------------
-- Same shape deployment_runs (021) already uses: an appended `log` column
-- streamed to the browser over Realtime. A five-minute apt-get behind a
-- spinner with no output, no history and a browser timeout is not an
-- acceptable way to install software on someone's server.
--
-- `log` is REDACTED before it is stored: it is readable by any org member,
-- while the credentials that produced it are not.
create table public.agent_server_jobs (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  agent_server_id uuid not null,
  slot_id uuid,

  kind text not null
    check (kind in ('provision', 'add_slot', 'update', 'restart',
                    'remove_slot', 'teardown', 'probe')),
  -- 'partial': an update that skipped a slot still running work (US-26.8).
  -- It is not a success and it is not a failure, and saying "succeeded"
  -- would hide a box left on old code.
  status text not null default 'queued'
    check (status in ('queued', 'running', 'succeeded', 'partial', 'failed')),

  step text,                            -- the step currently running / that failed
  log text not null default '',
  error text,

  started_by uuid,
  started_by_email text not null default '',
  started_at timestamptz,
  finished_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  foreign key (agent_server_id, org_id)
    references public.agent_servers (id, org_id) on delete cascade,
  foreign key (slot_id, org_id)
    references public.agent_slots (id, org_id) on delete set null
);

create index agent_server_jobs_org_idx on public.agent_server_jobs (org_id);
create index agent_server_jobs_server_idx
  on public.agent_server_jobs (agent_server_id, created_at desc);

-- one job at a time per host: concurrent installs on one machine would
-- fight over the same systemd units and the same install root
create unique index agent_server_jobs_one_active_key
  on public.agent_server_jobs (agent_server_id)
  where status in ('queued', 'running');

alter table public.agent_server_jobs enable row level security;

create policy "org members read agent server jobs"
  on public.agent_server_jobs for select
  using (public.is_org_member(org_id));

create trigger agent_server_jobs_touch
  before update on public.agent_server_jobs
  for each row execute function public.touch_updated_at();

-- ---------------------------------------------------------------------------
-- runner_config.paused — the one change outside the new tables (US-26.5)
-- ---------------------------------------------------------------------------
-- Slots come up paused: the service runs and the supervisor connects, but it
-- claims nothing until an admin enables it. workers.status is only
-- active|revoked, and revoking would invalidate the token just written to the
-- machine — the agent would drop off entirely and could not be resumed
-- without a re-provision. So pause gets its own column, enforced in the claim
-- path and pushed over the existing config.update socket frame.
--
-- The same knob drains a slot before an update restarts it (US-26.8).
alter table public.runner_config
  add column if not exists paused boolean not null default false;

-- Live fleet state in the UI: job logs stream as they are appended, and host
-- and slot state changes land without polling.
alter publication supabase_realtime add table public.agent_servers;
alter publication supabase_realtime add table public.agent_slots;
alter publication supabase_realtime add table public.agent_server_jobs;
