-- 099_runner_sessions: supervisor-runner presence (US-10.1).
--
-- A supervisor runner (apps/runner) holds a persistent control socket to the
-- API (WebSocket + JSON-RPC). Each connection is logged here so the
-- Team/Workers surface can show who is online, on what host, and which agent
-- modules it reported. Rows are written server-side (service role) only; the
-- client just reads its own org's presence.

create table public.runner_sessions (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  worker_id uuid not null references public.workers(id) on delete cascade,
  connected_at timestamptz not null default now(),
  disconnected_at timestamptz,
  last_seen_at timestamptz not null default now(),
  host_info jsonb not null default '{}'::jsonb,
  agent_versions jsonb not null default '{}'::jsonb,
  modules_available text[] not null default '{}'
);

create index runner_sessions_worker_idx
  on public.runner_sessions (worker_id, connected_at desc);

-- At most one live session per worker is expected; this partial index makes
-- "is this worker online" a cheap lookup and the reaper's job explicit.
create index runner_sessions_live_idx
  on public.runner_sessions (worker_id)
  where disconnected_at is null;

alter table public.runner_sessions enable row level security;

-- Org members read their org's runner presence. No client insert/update/delete
-- policies: sessions are written only by the API's service role (which bypasses
-- RLS), never by the browser.
create policy "org members read runner sessions"
  on public.runner_sessions for select
  using (public.is_org_member(org_id));

-- Live presence in the management UI (US-10.9).
alter publication supabase_realtime add table public.runner_sessions;
