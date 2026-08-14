-- US-91.11 / US-91.14 — an agent's work becomes a measured quantity, and an
-- item shows what it cost.
--
-- Almost everything needed was already recorded per run: lines_added /
-- lines_removed / files_changed (parsed from the diff), tokens_in / tokens_out
-- / cost_usd, the worker that held it, and the issue it was against. What was
-- missing was TIME, and any rollup cheap enough to read on a page load.
--
-- Time is stored in SECONDS. Hours are a rendering.
--
-- Why triggers rather than application code: the rollup must never drift from
-- the runs it summarises, and several code paths close a run (the worker
-- reporting, the reaper, a cancel, an abandon). A trigger runs in the same
-- transaction as whichever one of them wins.

-- ---------------------------------------------------------------------------
-- 1. The per-run measurement
-- ---------------------------------------------------------------------------

alter table public.runs
  add column if not exists work_seconds integer;

comment on column public.runs.work_seconds is
  'US-91.11: seconds this agent actually held the run — claimed_at to '
  'finished_at, or to last_heartbeat_at for a run that died holding its claim '
  '(the last moment it was demonstrably alive). Written once when the run '
  'reaches a terminal state and never recomputed on read. 0, never null, on a '
  'terminal run: a silent null becomes a silent zero in every sum downstream. '
  'KNOWN GAP: paused spans are NOT subtracted. Nothing in this schema records '
  'a pause interval — runs.paused_at is the current parked marker, cleared on '
  'resume — so a run paused for three hours and resumed counts those hours. '
  'Subtracting them needs a pause ledger, which is its own change.';

-- ---------------------------------------------------------------------------
-- 2. The daily rollup
-- ---------------------------------------------------------------------------

create table if not exists public.agent_effort_daily (
  org_id        uuid not null references public.organizations(id) on delete cascade,
  worker_id     uuid not null references public.workers(id) on delete cascade,
  day           date not null,
  work_seconds     bigint  not null default 0,
  runs_finished    integer not null default 0,
  issues_completed integer not null default 0,
  lines_added      bigint  not null default 0,
  lines_removed    bigint  not null default 0,
  files_changed    bigint  not null default 0,
  tokens_in        bigint  not null default 0,
  tokens_out       bigint  not null default 0,
  cost_usd         numeric not null default 0,
  updated_at    timestamptz not null default now(),
  primary key (org_id, worker_id, day)
);

comment on table public.agent_effort_daily is
  'US-91.11: what each agent did, per day. Incremented as each run reaches a '
  'terminal state, in the same transaction that closes it. Read by the Team '
  'page instead of aggregating every run in the workspace on every load.';

create index if not exists agent_effort_daily_org_day_idx
  on public.agent_effort_daily (org_id, day desc);
-- Every foreign key gets its index (us-87.9's rule, applied on the way in).
create index if not exists agent_effort_daily_worker_idx
  on public.agent_effort_daily (worker_id);

alter table public.agent_effort_daily enable row level security;

drop policy if exists agent_effort_daily_select on public.agent_effort_daily;
create policy agent_effort_daily_select on public.agent_effort_daily
  for select using (public.is_org_member(org_id));

-- Writes happen through the security-definer trigger below (and the service
-- role). No client-side insert/update/delete policy: these are derived facts.

-- ---------------------------------------------------------------------------
-- 3. What an item cost (US-91.14)
-- ---------------------------------------------------------------------------

alter table public.issues
  add column if not exists cost_usd numeric not null default 0;

comment on column public.issues.cost_usd is
  'US-91.14: what this work item has cost across ALL of its runs — failed, '
  'cancelled, abandoned and superseded attempts included. A story that took '
  'four tries cost what four tries cost; hiding that is the one thing this '
  'number must not do. Maintained by trigger from runs.cost_usd.';

-- ---------------------------------------------------------------------------
-- 4. The trigger that keeps them true
-- ---------------------------------------------------------------------------

create or replace function public.runs_effort_rollup()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_seconds integer;
  v_ended   timestamptz;
begin
  -- Only when a run ARRIVES at a terminal state, and only once.
  if new.status not in ('succeeded', 'failed', 'cancelled', 'abandoned', 'stopped') then
    return new;
  end if;
  if old.status = new.status and old.work_seconds is not null then
    return new;
  end if;

  v_ended := coalesce(new.finished_at, new.last_heartbeat_at, now());
  if new.claimed_at is null or v_ended < new.claimed_at then
    v_seconds := 0;
  else
    v_seconds := floor(extract(epoch from (v_ended - new.claimed_at)))::int;
  end if;
  new.work_seconds := v_seconds;

  if new.worker_id is not null then
    insert into public.agent_effort_daily as a (
      org_id, worker_id, day, work_seconds, runs_finished,
      lines_added, lines_removed, files_changed, tokens_in, tokens_out, cost_usd
    )
    values (
      new.org_id, new.worker_id, (v_ended at time zone 'utc')::date, v_seconds, 1,
      coalesce(new.lines_added, 0), coalesce(new.lines_removed, 0),
      coalesce(new.files_changed, 0), coalesce(new.tokens_in, 0),
      coalesce(new.tokens_out, 0), coalesce(new.cost_usd, 0)
    )
    on conflict (org_id, worker_id, day) do update set
      work_seconds  = a.work_seconds  + excluded.work_seconds,
      runs_finished = a.runs_finished + excluded.runs_finished,
      lines_added   = a.lines_added   + excluded.lines_added,
      lines_removed = a.lines_removed + excluded.lines_removed,
      files_changed = a.files_changed + excluded.files_changed,
      tokens_in     = a.tokens_in     + excluded.tokens_in,
      tokens_out    = a.tokens_out    + excluded.tokens_out,
      cost_usd      = a.cost_usd      + excluded.cost_usd,
      updated_at    = now();
  end if;

  -- US-91.14: what the item has cost, summed over every run against it.
  if new.issue_id is not null then
    update public.issues i
       set cost_usd = coalesce((
             select sum(coalesce(r.cost_usd, 0))
               from public.runs r
              where r.issue_id = i.id
           ), 0)
     where i.id = new.issue_id;
  end if;

  return new;
end;
$$;

drop trigger if exists runs_effort_rollup_trg on public.runs;
create trigger runs_effort_rollup_trg
  before update on public.runs
  for each row
  execute function public.runs_effort_rollup();

-- A work item counts as completed ONCE, for the agent whose code run produced
-- the merge — not per run, not per attempt, and never for a plan run. Merging
-- is a single status transition, which is what makes "once" true here.
create or replace function public.issues_completed_rollup()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_worker uuid;
  v_day    date;
begin
  if new.status <> 'merged' or old.status = 'merged' then
    return new;
  end if;

  select r.worker_id, (coalesce(r.finished_at, now()) at time zone 'utc')::date
    into v_worker, v_day
    from public.runs r
   where r.issue_id = new.id
     and r.kind = 'code'
     and r.status = 'succeeded'
     and r.worker_id is not null
   order by r.finished_at desc nulls last
   limit 1;

  if v_worker is null then
    return new;  -- merged by hand, or by a path with no agent behind it
  end if;

  insert into public.agent_effort_daily as a (org_id, worker_id, day, issues_completed)
  values (new.org_id, v_worker, v_day, 1)
  on conflict (org_id, worker_id, day) do update set
    issues_completed = a.issues_completed + 1,
    updated_at = now();

  return new;
end;
$$;

drop trigger if exists issues_completed_rollup_trg on public.issues;
create trigger issues_completed_rollup_trg
  after update of status on public.issues
  for each row
  execute function public.issues_completed_rollup();

-- ---------------------------------------------------------------------------
-- 5. Backfill — the Team page must not open on zeroes with months of real
--    work behind it.
-- ---------------------------------------------------------------------------

update public.runs
   set work_seconds = greatest(
         0,
         floor(extract(epoch from (
           coalesce(finished_at, last_heartbeat_at, claimed_at) - claimed_at
         )))::int
       )
 where work_seconds is null
   and claimed_at is not null
   and status in ('succeeded', 'failed', 'cancelled', 'abandoned', 'stopped');

-- Runs whose time cannot be established are zeroed explicitly, so the column
-- means "measured" rather than "maybe measured".
update public.runs
   set work_seconds = 0
 where work_seconds is null
   and status in ('succeeded', 'failed', 'cancelled', 'abandoned', 'stopped');

insert into public.agent_effort_daily (
  org_id, worker_id, day, work_seconds, runs_finished,
  lines_added, lines_removed, files_changed, tokens_in, tokens_out, cost_usd
)
select r.org_id,
       r.worker_id,
       (coalesce(r.finished_at, r.claimed_at) at time zone 'utc')::date as day,
       sum(coalesce(r.work_seconds, 0)),
       count(*),
       sum(coalesce(r.lines_added, 0)),
       sum(coalesce(r.lines_removed, 0)),
       sum(coalesce(r.files_changed, 0)),
       sum(coalesce(r.tokens_in, 0)),
       sum(coalesce(r.tokens_out, 0)),
       sum(coalesce(r.cost_usd, 0))
  from public.runs r
 where r.worker_id is not null
   and r.status in ('succeeded', 'failed', 'cancelled', 'abandoned', 'stopped')
   and coalesce(r.finished_at, r.claimed_at) is not null
 group by r.org_id, r.worker_id, day
on conflict (org_id, worker_id, day) do nothing;

-- Completed items, attributed the same way the trigger will from now on.
with merged_by_agent as (
  select i.org_id,
         r.worker_id,
         (coalesce(r.finished_at, now()) at time zone 'utc')::date as day,
         count(*) as n
    from public.issues i
    join lateral (
      select r2.worker_id, r2.finished_at
        from public.runs r2
       where r2.issue_id = i.id
         and r2.kind = 'code'
         and r2.status = 'succeeded'
         and r2.worker_id is not null
       order by r2.finished_at desc nulls last
       limit 1
    ) r on true
   where i.status = 'merged'
   group by i.org_id, r.worker_id, day
)
insert into public.agent_effort_daily (org_id, worker_id, day, issues_completed)
select org_id, worker_id, day, n from merged_by_agent
on conflict (org_id, worker_id, day) do update set
  issues_completed = public.agent_effort_daily.issues_completed + excluded.issues_completed,
  updated_at = now();

update public.issues i
   set cost_usd = coalesce(c.total, 0)
  from (
    select issue_id, sum(coalesce(cost_usd, 0)) as total
      from public.runs
     where issue_id is not null
     group by issue_id
  ) c
 where c.issue_id = i.id
   and i.cost_usd is distinct from coalesce(c.total, 0);
