-- 128_dashboard_incident_dismissals: acknowledge a dead-run incident so it
-- leaves the dashboard banner, org-wide (US-15.18).
--
-- The "Runs that died holding their claim" banner (us-13.6) is derived read-only
-- from issue_events; there was no way to make one go away, so a stalled factory
-- buried the actual work under a wall of failure diagnoses. This records an
-- org-wide acknowledgement keyed by the incident's issue_events.id, so a cleared
-- incident stops showing for the whole team. Because the banner shows the LATEST
-- death per issue, a newer death is a new event id and reappears — dismissing
-- acknowledges this death, not the issue forever.

create table if not exists public.dashboard_incident_dismissals (
  id bigint generated always as identity primary key,
  org_id uuid not null references public.organizations(id) on delete cascade,
  event_id uuid not null references public.issue_events(id) on delete cascade,
  -- who acknowledged it (the Supabase user), for the record; nullable so a
  -- future server-side path can dismiss without a session.
  dismissed_by uuid default auth.uid(),
  dismissed_at timestamptz not null default now(),
  unique (org_id, event_id)
);

comment on table public.dashboard_incident_dismissals is
  'US-15.18: an org-wide acknowledgement that a dead-run incident (issue_events.id) '
  'has been seen and should leave the dashboard banner. A newer death on the same '
  'issue is a new event id and reappears.';

create index if not exists dashboard_incident_dismissals_org_idx
  on public.dashboard_incident_dismissals (org_id, event_id);

alter table public.dashboard_incident_dismissals enable row level security;

-- Read + insert for org members only; org-scoped by is_org_member so a dismissal
-- in one org never hides another org's incident. No update/delete policy — a
-- dismissal is an acknowledgement, not something to unset (a newer death makes a
-- new row anyway).
drop policy if exists "members read their org incident dismissals"
  on public.dashboard_incident_dismissals;
create policy "members read their org incident dismissals"
  on public.dashboard_incident_dismissals for select
  using (public.is_org_member(org_id));

drop policy if exists "members dismiss their org incidents"
  on public.dashboard_incident_dismissals;
create policy "members dismiss their org incidents"
  on public.dashboard_incident_dismissals for insert
  with check (public.is_org_member(org_id));

-- Realtime so a dismissal in one manager's tab clears the banner in another's.
alter publication supabase_realtime add table public.dashboard_incident_dismissals;
