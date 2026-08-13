-- 192_external_deployment_runs: what an external run produced (US-50.2).
--
-- `commit_sha` keeps the meaning it already has — the SOURCE commit that was
-- deployed — because that is what GET /issues/{id}/deployments tests for
-- ancestry when it answers "is this work item live here". Recording the merge
-- commit there instead would silently break that panel.
--
-- So the merge commit and the pull request get their own nullable columns,
-- null for every factory run and for every row that already exists.

alter table public.deployment_runs
  add column merge_commit_sha text,
  add column pr_number int;

comment on column public.deployment_runs.merge_commit_sha is
  'US-50.2: the merge commit an external run created on the target branch. '
  'Null for factory runs.';

comment on column public.deployment_runs.pr_number is
  'US-50.2: the pull request an external run merged (or left open when GitHub '
  'refused). Null for factory runs.';
