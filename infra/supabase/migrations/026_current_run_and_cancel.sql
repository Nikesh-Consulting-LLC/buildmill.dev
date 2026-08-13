-- 026_current_run_and_cancel: deployed-commit visibility (US-1.34) and
-- run cancellation (US-1.35).
--
-- deployments.current_run_id is the durable "what is live on this
-- environment" pointer — set by `api` the moment a run succeeds
-- (deploys, rollbacks, and later redeploys alike), so future records
-- (test results, bug reports) can FK the exact deployment they were
-- observed on instead of re-deriving it from history.
--
-- deployment_runs.commit_message is captured at fetch time so the
-- "currently deployed" display always renders from local data even
-- when GitHub is unreachable.

alter table public.deployments
  add column current_run_id uuid
    references public.deployment_runs (id) on delete set null;

alter table public.deployment_runs
  drop constraint deployment_runs_status_check;
alter table public.deployment_runs
  add constraint deployment_runs_status_check
    check (status in ('queued', 'running', 'succeeded', 'failed', 'cancelled'));

alter table public.deployment_runs
  add column commit_message text,
  add column cancelled_by uuid,
  add column cancelled_by_email text;
