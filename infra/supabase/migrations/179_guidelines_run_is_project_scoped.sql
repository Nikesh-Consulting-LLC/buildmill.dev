-- 179_guidelines_run_is_project_scoped: US-43.6.
--
-- us-43.2 modelled a guidelines refresh as a CHORE, for the display id, the
-- Work Items row and the comment thread. The first live use showed what that
-- costs: us-43.1 moves the chore to `in-review` at hand-back, `in-review` is a
-- waiting status, and so one decision produced two Things to Do entries — the
-- Guidelines refresh card AND a "needs your code review" row leading to the
-- code-review gate, offering approve/reject over a branch, a diff and a pull
-- request that a guidelines run never produces.
--
-- No amount of suppression on individual surfaces fixes that, because it is a
-- category error: a refresh is not delivery work. It becomes a project-scoped
-- run instead, beside `release` and `deploy` — and every symptom above goes
-- away by construction. With no issue there is no status to set, so the review
-- gate is not merely hidden, it is unreachable.
--
-- One constraint. The rest is application code.

alter table public.runs drop constraint if exists runs_issue_or_project_scoped;
alter table public.runs
  add constraint runs_issue_or_project_scoped
  check (issue_id is not null or kind in ('release', 'deploy', 'guidelines'));

comment on constraint runs_issue_or_project_scoped on public.runs is
  'US-13.12/US-13.13/US-43.6: which run kinds may exist without a work item. '
  'These are the runs that act on a PROJECT rather than on an item in it — a '
  'release cut, a deployment, and a guidelines refresh. Every other kind must '
  'name the issue it serves.';
