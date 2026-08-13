-- US-34.2 + US-34.3 + US-34.4: the tools an agent actually gets, how it reaches
-- them, and the record of what it did with them.
--
-- us-34.1 registered the servers and put their credentials in Vault. Nothing in
-- it reached an agent. These three land the part that does — and they land
-- together because the scoped key, the grant that decides which servers it can
-- reach, and the audit of what came through it are one security surface. Split
-- across three migrations they would each be reviewable and the seam between
-- them would not.

-- ---------------------------------------------------------------------------
-- US-34.2: a scoped key, per run
-- ---------------------------------------------------------------------------
-- Scoped exactly the way a gateway key is (migration 101): minted for one run,
-- dead when the run ends. A leaked key is worth one finished run. That property
-- is what makes proxying third-party credentials safe at all, so it is not
-- optional and it is not configurable.
create table public.mcp_scoped_keys (
  id uuid primary key default gen_random_uuid(),
  key_hash text not null unique,
  org_id uuid not null references public.organizations(id) on delete cascade,
  worker_id uuid not null references public.workers(id) on delete cascade,
  run_id uuid not null,
  created_at timestamptz not null default now(),
  expires_at timestamptz not null,
  revoked_at timestamptz
);

create index mcp_scoped_keys_hash_idx on public.mcp_scoped_keys (key_hash);
create index mcp_scoped_keys_run_idx on public.mcp_scoped_keys (run_id);

alter table public.mcp_scoped_keys enable row level security;
-- No policies, on purpose: only the service role touches this table. A key is
-- not something a browser has any reason to read, and `llm_gateway_keys` set
-- this precedent for the same reason.

-- ---------------------------------------------------------------------------
-- US-34.3: which servers a run gets
-- ---------------------------------------------------------------------------
-- A preset REFERENCES catalog entries; it never carries connection details. The
-- catalog owns where a server is and what it needs, so a server that moves does
-- not need every preset edited.
--
-- Default deny: a preset with no grants gets the factory server and nothing
-- else — the state us-31.9 ships. Registering a server grants it to no one until
-- a preset names it, so an admin adding a server cannot accidentally change how
-- every existing run behaves. The same fail-closed principle us-31.3 applies to
-- project grants, for the same reason.
alter table public.agent_presets
  add column if not exists tool_grants uuid[] not null default '{}';

comment on column public.agent_presets.tool_grants is
  'US-34.3: mcp_servers ids this preset grants. Empty = the factory server only.';

-- A project may WITHHOLD a server the preset grants. Presets are shared across
-- projects, and a database tool that is right for one is wrong for another. The
-- effective surface is the intersection: the preset asks, the project may refuse,
-- and a refusal is reported on the run rather than silently shrinking the
-- toolset.
alter table public.projects
  add column if not exists mcp_withheld uuid[] not null default '{}';

comment on column public.projects.mcp_withheld is
  'US-34.3: mcp_servers ids this project refuses regardless of the preset grant.';

-- What the run actually got, recorded beside its other resolved settings — so
-- what a run could REACH is as explainable afterwards as which model it used.
alter table public.runs
  add column if not exists tool_surface jsonb;

comment on column public.runs.tool_surface is
  'US-34.3: the effective MCP surface at claim: granted, withheld, unavailable, '
  'and which entries are proxied-and-audited vs local-and-not.';

-- ---------------------------------------------------------------------------
-- US-34.4: every proxied call, on the record
-- ---------------------------------------------------------------------------
-- The proxy is the recording point: it sees every credentialed call by
-- construction, in the same place that authenticates it. Asking the agent to
-- report its own tool use is the thing this exists to avoid.
--
-- The call, not the payload. Full arguments and results would put project data —
-- and potentially the very secrets the catalog protects — into a table any org
-- member can read. The shell audit made the same trade for the same reason.
create table public.mcp_tool_calls (
  id bigserial primary key,
  org_id uuid not null references public.organizations(id) on delete cascade,
  run_id uuid,
  worker_id uuid,
  server_id uuid,
  server_name text not null default '',

  tool text not null default '',
  -- Redacted, and conservatively: enough to know what was asked without becoming
  -- a second copy of the data. Anything resembling a credential is REMOVED
  -- rather than truncated, because a truncated secret is still a secret.
  arguments_redacted jsonb,

  outcome text not null default 'ok' check (outcome in ('ok', 'error', 'refused')),
  error text,
  duration_ms int,
  response_bytes int,

  created_at timestamptz not null default now()
);

create index mcp_tool_calls_run_idx on public.mcp_tool_calls (run_id, created_at desc);
create index mcp_tool_calls_org_idx on public.mcp_tool_calls (org_id, created_at desc);

alter table public.mcp_tool_calls enable row level security;

create policy "org members read their tool calls"
  on public.mcp_tool_calls for select
  using (public.is_org_member(org_id));

-- A record that cannot be written must not fail the call — the proxy's job is to
-- relay — but the gap must not vanish either. A silently lossy audit is worse
-- than none, because it is believed. So the drops are counted per run, and
-- surfaced beside the calls.
alter table public.runs
  add column if not exists tool_calls_dropped int not null default 0;

comment on column public.runs.tool_calls_dropped is
  'US-34.4: audit records that could not be written for this run. Non-zero means '
  '"we know we lost some", which is a different statement from "no calls made".';

create or replace function public.count_dropped_tool_call(p_run uuid)
returns void
language sql
as $$
  update public.runs set tool_calls_dropped = tool_calls_dropped + 1
  where id = p_run;
$$;
