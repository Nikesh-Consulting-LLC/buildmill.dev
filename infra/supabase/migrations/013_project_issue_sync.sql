-- 013_project_issue_sync: pull GitHub issues into tasks, push task status
-- back as open/closed (US-1.20). Sync is one-way status push (app ->
-- GitHub) plus an on-demand pull (GitHub -> app), not a live feed.

alter table public.projects
  add column issue_sync_enabled boolean not null default false,
  add column issue_sync_last_pulled_at timestamptz;

alter table public.tasks
  add column github_issue_number integer,
  add column github_issue_url text;

-- Prevents importing the same issue twice; multiple projects may each
-- have their own issue #1, hence scoped to (project_id, issue_number).
create unique index tasks_github_issue_unique
  on public.tasks (project_id, github_issue_number)
  where github_issue_number is not null;
