-- 121_run_trace: a durable, detailed per-run trace agents stream to (US-15.5).
--
-- run_activity (115) records only which tool a claim-holder called — the glance
-- (us-14.8). This is the record: entries an agent streams as it works — steps,
-- decisions, outputs, errors — kept so a run can be read long after it ends,
-- including a run that failed and left nothing on the operator's machine.
--
-- Attribution is from the claim, never the caller's say-so: record_run_trace
-- derives org, issue, and the acting principal from the run and its holding
-- worker, and refuses a run the caller doesn't hold — an agent cannot write
-- into another's trace.

create table if not exists public.run_trace (
  id bigint generated always as identity primary key,
  org_id uuid not null references public.organizations(id) on delete cascade,
  run_id uuid not null references public.runs(id) on delete cascade,
  issue_id uuid references public.issues(id) on delete cascade,
  -- the acting agent (workers.principal_id at claim time), for attribution.
  principal_id uuid,
  kind text not null check (kind in (
    'step', 'tool', 'decision', 'output', 'progress',
    'clarification', 'submission', 'error'
  )),
  content text not null,
  at timestamptz not null default now()
);

comment on table public.run_trace is
  'US-15.5: the durable per-run trace agents stream to. Entries are attributed '
  'to the run and holding principal by record_run_trace; content is what the '
  'agent chose to report.';

create index if not exists run_trace_run_idx
  on public.run_trace (run_id, at, id);

alter table public.run_trace enable row level security;

-- Read-only to org members; the API writes over its direct Postgres connection
-- (workers are not Supabase users), so there is no insert policy — same shape
-- as run_activity (115) / clarifications (059).
drop policy if exists "members read their org run trace" on public.run_trace;
create policy "members read their org run trace"
  on public.run_trace for select
  using (public.is_org_member(org_id));

-- Append one trace entry to a run the caller holds. Returns the new row id, or
-- null when the run isn't claimed by this worker (a clean refusal, not a spoof).
create or replace function public.record_run_trace(
  p_run uuid,
  p_worker uuid,
  p_kind text,
  p_content text
)
returns bigint
language plpgsql
security definer
set search_path to 'public'
as $$
declare
  v_run public.runs%rowtype;
  v_principal uuid;
  v_id bigint;
begin
  select * into v_run from public.runs
  where id = p_run and worker_id = p_worker and status = 'running';
  if not found then
    return null;  -- not this worker's live claim
  end if;

  select principal_id into v_principal from public.workers where id = p_worker;

  insert into public.run_trace (org_id, run_id, issue_id, principal_id, kind, content)
  values (v_run.org_id, p_run, v_run.issue_id, v_principal, p_kind, p_content)
  returning id into v_id;
  return v_id;
end;
$$;

-- Realtime: the run-detail page fills live while a run works.
alter publication supabase_realtime add table public.run_trace;
