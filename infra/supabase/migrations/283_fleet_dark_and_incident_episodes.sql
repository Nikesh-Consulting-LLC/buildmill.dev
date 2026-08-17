-- 283_fleet_dark_and_incident_episodes (us-116.8): the fleet says when it goes
-- dark, and says a standing fault once.
--
-- On 2026-08-17, 11:49–12:57 UTC, every agent on Pod-001 was offline for 68
-- minutes (migration 279 landed before its hotfix). Six grey pills for anyone
-- who opened the roster; 8,023 crash reports in the System issues inbox; no
-- notification, because no code looks for "the whole fleet dropped and none
-- came back". Meanwhile `raise_service_incident` re-raised a revoked-token
-- alarm on the hour, every hour, for as long as the condition stood — 429
-- `agent-token` incidents in fourteen days, 118 for one agent left revoked for
-- five days — each with a notification to the org's managers.
--
-- Two additions:
--
--   * `runner_incidents.cleared_at` — a standing fault is ONE incident for as
--     long as it stands. The probe clears it when it finds the condition gone
--     (`agent-service`: the unit is active again; `agent-token`: the worker is
--     active again), and only then may the same kind be raised again for that
--     worker. Open-since / cleared-at instead of a list of duplicates.
--   * `fleet_dark_episodes` — one row per fleet-wide outage in an org: opened
--     by the API's liveness loop when an org that had live agents has had none
--     for more than two minutes (above any deploy bounce and above the 90 s
--     presence window), closed when any agent returns. The notification and
--     the System issue hang off the row, so an episode is told once.

alter table public.runner_incidents
  add column if not exists cleared_at timestamptz;

comment on column public.runner_incidents.cleared_at is
  'us-116.8: when the probe found the standing condition gone. Null while '
  'the fault stands; the same kind is not raised again for the worker until '
  'this is set.';

create index if not exists runner_incidents_open_idx
  on public.runner_incidents (worker_id, kind)
  where cleared_at is null;

create table if not exists public.fleet_dark_episodes (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  started_at timestamptz not null,
  agent_count int not null default 0,
  notified_at timestamptz,
  app_issue_id uuid,
  ended_at timestamptz,
  created_at timestamptz not null default now()
);

comment on table public.fleet_dark_episodes is
  'us-116.8: one row per fleet-wide outage in an org — every agent offline '
  'for more than two minutes. Opened and closed by the API''s liveness loop; '
  'the manager notification and the platform System issue hang off the row '
  'so an episode is told once.';

create index if not exists fleet_dark_episodes_open_idx
  on public.fleet_dark_episodes (org_id)
  where ended_at is null;

alter table public.fleet_dark_episodes enable row level security;

create policy "org members read fleet dark episodes"
  on public.fleet_dark_episodes for select
  using (public.is_org_member(org_id));
