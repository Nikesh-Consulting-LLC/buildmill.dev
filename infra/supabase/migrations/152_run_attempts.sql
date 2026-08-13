-- 152_run_attempts: an agent stops retrying an item it cannot finish
-- (US-31.5).
--
-- `max_repair_attempts` bounds repair INSIDE one run. Beyond that boundary
-- nothing counted: 007 made a failed item retryable and US-27.11 sends a
-- failed build back to `building`, so `failed -> queued -> claimed -> failed`
-- was an unbounded loop that spent model tokens every lap.
--
-- WHY NOT COUNT `runs`: requeue_expired_claims does NOT create a new run when
-- a claim lapses — it mutates the existing row back to `queued` and NULLs
-- worker_id. So a requeued attempt leaves no trace in `runs` and loses the
-- agent that made it. The four-lap loop on 2026-07-26 produced ZERO failed
-- runs; a counter over `runs` would have counted zero. Hence an append-only
-- attempt log, keyed on worker **id** — never worker name, which US-32.2
-- makes editable and explicitly non-unique.
--
-- Four things consume an attempt: a failed run, a lease expiry without a
-- submission, a stale-heartbeat requeue (US-31.2), and a run stopped at its
-- ceiling (US-33.2, later). A CANCELLED run consumes nothing — the manager
-- withdrew it and the agent did nothing wrong.

create table public.run_attempts (
  id bigint generated always as identity primary key,
  org_id uuid not null references public.organizations(id) on delete cascade,
  issue_id uuid not null,
  run_id uuid,
  worker_id uuid,
  kind text not null,
  reason text not null check (reason in (
    'failed', 'lease-expired', 'heartbeat-stale', 'ceiling'
  )),
  created_at timestamptz not null default now(),
  foreign key (issue_id, org_id)
    references public.issues (id, org_id) on delete cascade
);

create index run_attempts_issue_idx on public.run_attempts (issue_id, created_at desc);
create index run_attempts_worker_issue_idx
  on public.run_attempts (worker_id, issue_id) where worker_id is not null;

alter table public.run_attempts enable row level security;

-- Read-only to org members; written by `api` with the service role, like
-- every other append-only audit trail here.
create policy "org members read run attempts"
  on public.run_attempts for select
  using (public.is_org_member(org_id));

comment on table public.run_attempts is
  'US-31.5: append-only log of consumed attempts against a work item. NOT '
  'derived from runs — a lease requeue mutates the run row and nulls '
  'worker_id, so run rows cannot answer "how many times has this agent '
  'tried". Keyed on worker id, never worker name (US-32.2 names are '
  'editable and non-unique).';

-- Per-agent cap ------------------------------------------------------------

alter table public.runner_config
  add column max_item_attempts int not null default 3
  check (max_item_attempts between 1 and 20);

comment on column public.runner_config.max_item_attempts is
  'US-31.5: after this many consumed attempts on one work item, THIS agent '
  'is no longer offered it and cannot claim it — the item returns to the '
  'pool for a different agent.';

-- Item ceiling (org-wide) --------------------------------------------------

alter table public.organizations
  add column max_item_attempts int not null default 5
  check (max_item_attempts between 1 and 50);

comment on column public.organizations.max_item_attempts is
  'US-31.5: after this many consumed attempts by ANY agent, the item stops '
  'being dispatched and becomes the manager''s problem. Without it an item '
  'bounces around the fleet forever.';

-- Blocked state ------------------------------------------------------------

alter table public.issues
  add column attempts_blocked_at timestamptz;

comment on column public.issues.attempts_blocked_at is
  'US-31.5: set when the item ceiling is reached; cleared by an explicit '
  'manager release. While set, the item is not dispatchable.';

-- Helpers ------------------------------------------------------------------

create or replace function public.issue_attempt_count(p_issue uuid)
returns int
language sql
stable
set search_path = ''
as $$
  select count(*)::int from public.run_attempts where issue_id = p_issue;
$$;

create or replace function public.worker_attempt_count(p_worker uuid, p_issue uuid)
returns int
language sql
stable
set search_path = ''
as $$
  select count(*)::int from public.run_attempts
  where issue_id = p_issue and worker_id = p_worker;
$$;

-- THE offer predicate: has this agent burned its per-agent cap on this item?
create or replace function public.worker_exhausted_on_issue(
  p_worker uuid, p_issue uuid
) returns boolean
language sql
stable
set search_path = ''
as $$
  select public.worker_attempt_count(p_worker, p_issue) >= coalesce(
    (select rc.max_item_attempts from public.runner_config rc
      where rc.worker_id = p_worker),
    3
  );
$$;

comment on function public.worker_exhausted_on_issue(uuid, uuid) is
  'US-31.5: true when this agent has spent its per-agent attempt cap on this '
  'work item. The pool listing and the claim gate both call it, so an agent '
  'is never offered work it is about to be refused.';

-- Dispatch refuses a blocked item -----------------------------------------
--
-- Deliberately a TRIGGER, not a rewrite of dispatch_issue. Two reasons:
--   1. There is more than one way to create a run — dispatch_issue,
--      dispatch_breakdown, feature_dispatch_phase (US-17.2's build modes),
--      dispatch_test_run, dispatch_deploy_run, dispatch_release_for. A guard
--      inside one of them leaves the others open, and the auto-approve paths
--      are exactly the ones that would keep burning attempts unattended.
--   2. dispatch_issue is a long function assembling guidelines, learnings,
--      documents, test cases and seeded instructions. Re-declaring it to add
--      two lines risks silently dropping any of that.

create or replace function public.refuse_run_on_exhausted_item()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  v_issue public.issues%rowtype;
  v_attempts int;
  v_ceiling int;
begin
  if new.issue_id is null then
    return new;  -- issue-less runs (deploy, release) are not item-capped
  end if;
  select * into v_issue from public.issues where id = new.issue_id;
  if not found then
    return new;
  end if;

  if v_issue.attempts_blocked_at is not null then
    raise exception 'this item has exhausted its attempts — review the failures and release it to try again';
  end if;

  v_attempts := public.issue_attempt_count(new.issue_id);
  select o.max_item_attempts into v_ceiling
  from public.organizations o where o.id = v_issue.org_id;
  if v_attempts >= coalesce(v_ceiling, 5) then
    -- Latch it, so the manager sees a blocked item rather than a refusal
    -- that repeats every time something tries.
    update public.issues set attempts_blocked_at = now() where id = new.issue_id;
    insert into public.issue_events (org_id, issue_id, type, payload)
    values (v_issue.org_id, new.issue_id, 'attempts-exhausted',
            jsonb_build_object('attempts', v_attempts,
                               'ceiling', coalesce(v_ceiling, 5)));
    raise exception 'this item has exhausted its attempts — review the failures and release it to try again';
  end if;
  return new;
end;
$$;

create trigger runs_refuse_on_exhausted_item
  before insert on public.runs
  for each row execute function public.refuse_run_on_exhausted_item();

comment on function public.refuse_run_on_exhausted_item() is
  'US-31.5: the item ceiling, enforced at the one place every dispatch path '
  'must pass — inserting the run. Covers dispatch_issue, the build-mode and '
  'auto-approve paths, and anything added later, without re-declaring any of '
  'them.';
