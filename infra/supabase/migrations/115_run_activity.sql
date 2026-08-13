-- US-14.8: the factory narrates the run itself.
--
-- Migration 109 already intercepts every MCP tool call a claim-holder
-- makes, to extend the lease — and keeps only the timestamp, discarding
-- *which* tool ran. The factory watches the agent work and throws the
-- account away, which is why a 12-minute code run showed one note written
-- before any work began and then eleven minutes of nothing.
--
-- This records the tool alongside the heartbeat. No new call path, no
-- agent cooperation, no tool signature changes: the same intercept, one
-- column more.
--
-- Two deliberate bounds:
--
-- 1) Only the tool NAME and its run. Never arguments — file contents,
--    submitted diffs and note text stay where they already live, and a
--    trace that cannot leak them cannot leak them by accident later.
--
-- 2) Transitions, not calls. A worker reading forty files produces one
--    row, not forty: an insert is skipped when the run's most recent
--    activity is the same tool within the coalesce window. That keeps
--    the table proportional to what a manager would actually read, and
--    keeps a chatty agent from writing thousands of rows per run.

create table if not exists public.run_activity (
  id bigint generated always as identity primary key,
  org_id uuid not null references public.organizations(id) on delete cascade,
  run_id uuid not null references public.runs(id) on delete cascade,
  tool text not null,
  at timestamptz not null default now()
);

comment on table public.run_activity is
  'US-14.8: which MCP tool a claim-holding worker called, and when. '
  'Derived narration for a run in flight — tool name only, never '
  'arguments. Consecutive identical calls coalesce (see record_run_activity).';

create index if not exists run_activity_run_idx
  on public.run_activity (run_id, at desc);

alter table public.run_activity enable row level security;

-- Read-only to org members; the API writes over its direct Postgres
-- connection (workers are not Supabase users), so there is no insert
-- policy — same shape as clarifications (059).
drop policy if exists "members read their org run activity" on public.run_activity;
create policy "members read their org run activity"
  on public.run_activity for select
  using (public.is_org_member(org_id));

-- The coalescing insert. Returns true when a row was actually written.
create or replace function public.record_run_activity(
  p_run uuid,
  p_tool text,
  p_coalesce_seconds int default 45
)
returns boolean
language plpgsql
security definer
set search_path to 'public'
as $$
declare
  v_org uuid;
  v_last_tool text;
  v_last_at timestamptz;
begin
  select org_id into v_org from public.runs where id = p_run;
  if v_org is null then
    return false;
  end if;

  select tool, at into v_last_tool, v_last_at
  from public.run_activity
  where run_id = p_run
  order by at desc
  limit 1;

  -- Same tool, still inside the window: the run is doing what it was
  -- already doing, and a second row would say nothing new.
  if v_last_tool = p_tool
     and v_last_at > now() - make_interval(secs => p_coalesce_seconds) then
    return false;
  end if;

  insert into public.run_activity (org_id, run_id, tool)
  values (v_org, p_run, p_tool);
  return true;
end;
$$;

comment on function public.record_run_activity(uuid, text, int) is
  'US-14.8: record one MCP tool call against a run, coalescing repeats of '
  'the same tool inside the window. Returns true when a row was written.';

revoke execute on function public.record_run_activity(uuid, text, int) from anon, authenticated;

-- The work item's live panel subscribes to this, so "what is it doing"
-- updates while the manager watches instead of on the next refresh.
alter publication supabase_realtime add table public.run_activity;
