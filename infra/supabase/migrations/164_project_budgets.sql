-- US-37.1 / US-37.2 — a project can be given a budget, and it is the only
-- thing that stops work.
--
-- Money was metered per project from the first line of us-33.1: every
-- llm_usage row carries project_id. What did not exist was a number to
-- compare it against. The only limit in the app was us-33.2's per-RUN
-- ceiling, which is the wrong unit — it stops one task mid-work for a
-- figure the manager never set on anything they think about, while the
-- project it belongs to has no limit at all.

-- The budget ---------------------------------------------------------------
--
-- Opt-in. Every project today has no budget, and defaulting them to a number
-- would stop work on projects nobody has priced.
--
-- Two columns rather than one nullable amount, so turning a budget off does
-- not throw away the figure you would turn back on.
alter table public.projects
  add column if not exists budget_enabled boolean not null default false,
  add column if not exists budget_usd numeric(12,2),
  -- Spend counts from here, not from the beginning of time. Without it,
  -- enabling a $50 budget on a project that has already spent $43 reads as
  -- instantly exhausted, and the manager's first experience of the feature is
  -- work stopping for a reason that predates the decision. Re-stamping it is
  -- how a manager starts a new month or a new stretch of work.
  add column if not exists budget_started_at timestamptz;

comment on column public.projects.budget_enabled is
  'US-37.1: whether this project has a spend budget. Off for every existing '
  'project; a budget nobody set must never stop work.';
comment on column public.projects.budget_usd is
  'US-37.1: dollars this project may spend since budget_started_at. Kept when '
  'the budget is switched off so it can be switched back on.';
comment on column public.projects.budget_started_at is
  'US-37.1: spend counts from here. Stamped when the budget is first enabled '
  'so prior spend does not exhaust it on day one; re-stamped to reset.';

-- llm_usage already carries project_id, but no index leads with it: the
-- attribution index is (org_id, worker_id, project_id, model), which cannot
-- serve "this project, since this timestamp". That sum now runs on the run
-- insert path, so it needs its own.
create index if not exists llm_usage_project_idx
  on public.llm_usage (project_id, created_at desc)
  where project_id is not null;

-- What a project has spent -------------------------------------------------
--
-- Computed, never counted into a column. us-33.1 made usage rows append-only
-- events with every aggregate a read-time query, deliberately — a counter
-- column drifts the first time a write is retried. This follows it.
create or replace function public.project_spend_usd(p_project uuid)
returns numeric
language sql
stable
set search_path = ''
as $$
  select coalesce(sum(u.cost_usd), 0)
  from public.llm_usage u
  join public.projects p on p.id = u.project_id
  where u.project_id = p_project
    and u.created_at >= coalesce(p.budget_started_at, '-infinity'::timestamptz);
$$;

comment on function public.project_spend_usd(uuid) is
  'US-37.1: dollars spent on a project since its budget_started_at. Calls on '
  'models with no rate contribute null and are excluded by sum() — unknown '
  'cost is not free, and the surfaces show the unmeasured count beside this.';

-- The gate -----------------------------------------------------------------
--
-- A trigger on runs insert, not a rewrite of dispatch_issue — for exactly the
-- reasons us-31.5 recorded when it made the same call in migration 152:
--   1. There is more than one way to create a run (dispatch_issue,
--      dispatch_breakdown, feature_dispatch_phase, dispatch_test_run,
--      dispatch_deploy_run, dispatch_release_for). A guard inside one leaves
--      the others open, and the auto-approve paths are precisely the ones
--      that would run a budget down unattended.
--   2. dispatch_issue is long and assembles guidelines, learnings, documents
--      and test cases; re-declaring it to add two lines risks dropping any of
--      that.
--
-- It refuses to START work. A run already going is left alone to finish:
-- killing a task at 90% is the us-33.2 behaviour being removed, and half a
-- task is worth less than the money it already cost. The consequence, stated
-- rather than discovered later: one runaway run can carry a project past its
-- budget between checks. That overrun is bounded by a single run, and
-- us-31.2's run timeout bounds that.
create or replace function public.refuse_run_on_exhausted_budget()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  v_project public.projects%rowtype;
  v_spent numeric;
begin
  select * into v_project from public.projects where id = new.project_id;
  if not found or not v_project.budget_enabled or v_project.budget_usd is null then
    return new;
  end if;

  v_spent := public.project_spend_usd(new.project_id);
  if v_spent < v_project.budget_usd then
    return new;
  end if;

  -- Names its own release valve. A refusal that does not say how to clear it
  -- is a dead end, and "raise the number" is the whole point of moving the
  -- budget to this level.
  raise exception
    '% has spent $% of its $% budget. Raise the budget or reset its counter on the project''s Overview tab to dispatch again.',
    v_project.name,
    to_char(v_spent, 'FM999999990.00'),
    to_char(v_project.budget_usd, 'FM999999990.00');
end;
$$;

create trigger runs_refuse_on_exhausted_budget
  before insert on public.runs
  for each row execute function public.refuse_run_on_exhausted_budget();

comment on function public.refuse_run_on_exhausted_budget() is
  'US-37.2: the project budget, enforced at the one place every dispatch path '
  'must pass — inserting the run. Refuses to start work; never stops a run '
  'already going. Deliberately does NOT consume an item attempt: being out of '
  'money is not an agent looping on something it cannot do, and burning '
  'attempts would mean raising the budget is no longer enough to resume.';

-- Retiring the per-run ceiling ---------------------------------------------
--
-- us-33.2's max_budget_usd is removed rather than left switched off. A preset
-- control that no longer does anything is the shown-vs-enforced divergence
-- us-14.6 exists to prevent. The gateway's refusal goes with it in the API.
--
-- runs.resolved_settings is left exactly as it is on historical rows: those
-- records say what those runs were actually given, and rewriting them would
-- make the incident of 2026-07-27 unreadable.
update public.agent_presets
set settings = settings - 'max_budget_usd'
where settings ? 'max_budget_usd';

-- One read for a whole list ------------------------------------------------
--
-- The projects page shows spend on every card. One row per project from one
-- query, not one call per card: the list already does per-card enrichment for
-- people and deployments, and adding an N+1 to it for money is how a page gets
-- slow. Invoker rights, so llm_usage's org-member RLS is what scopes it.
create or replace function public.org_project_spend(p_org uuid)
returns table (
  project_id uuid,
  spent_usd numeric,
  unmeasured_calls bigint
)
language sql
stable
set search_path = ''
as $$
  select p.id,
         coalesce(sum(u.cost_usd), 0),
         count(*) filter (where u.id is not null and u.cost_usd is null)
  from public.projects p
  left join public.llm_usage u
    on u.project_id = p.id
   and u.created_at >= coalesce(p.budget_started_at, '-infinity'::timestamptz)
  where p.org_id = p_org
  group by p.id;
$$;

comment on function public.org_project_spend(uuid) is
  'US-37.4: spend for every project in an org, one row each, since each '
  'project''s own budget_started_at. unmeasured_calls is the count of calls on '
  'models with no rate — money that is real and that no budget can see, so the '
  'card says so rather than showing a percentage that quietly omits it.';
