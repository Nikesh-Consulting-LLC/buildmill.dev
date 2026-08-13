-- 063_agent_test_runs: agent-sourced test runs (US-5.19).
-- report_test_results records a worker's pass/fail/blocked outcomes
-- against the issue's materialized test cases as a real test run — the
-- same mechanism the Tests pages and the us-2.6 merge gate read, so an
-- agent-reported pass lifts the "unrun" override requirement.
--
-- test_runs grows attribution: source distinguishes agent runs, which
-- have no auth user (started_by becomes nullable) but carry the worker
-- and the factory run they verified. test_run_results gains a 'blocked'
-- outcome (environment/data prevented execution) — the gate treats it
-- like a failure (warn), unlike 'skipped' (unrun).

alter table public.test_runs
  alter column started_by drop not null;

alter table public.test_runs
  add column source text not null default 'human'
    check (source in ('human', 'agent')),
  add column worker_id uuid references public.workers(id) on delete set null,
  add column run_id uuid references public.runs(id) on delete set null,
  add column worker_name text not null default '';

-- Human runs keep requiring their user; agent runs may outlive their
-- worker row (on delete set null), so only 'human' is constrained.
alter table public.test_runs
  add constraint test_runs_attribution check (
    source = 'agent' or started_by is not null
  );

create index test_runs_factory_run_idx on public.test_runs (run_id)
  where run_id is not null;

alter table public.test_run_results
  drop constraint test_run_results_result_check;
alter table public.test_run_results
  add constraint test_run_results_result_check
    check (result in ('pending', 'pass', 'fail', 'skipped', 'blocked'));
