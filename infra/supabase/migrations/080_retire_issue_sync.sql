-- 080_retire_issue_sync: drop the now-unused GitHub Issue sync plumbing
-- (US-7.6). Requirements live in Build Mill only; the sync surface, the pull
-- endpoint, and the status push-back are removed in the same change.
--
-- Only the two sync-state columns are dropped. issues.github_issue_number /
-- github_issue_url are deliberately KEPT as harmless inert columns: previously
-- imported issues retain their content (no data loss) and issue search still
-- matches on the number. They carry no sync behavior anymore.

alter table public.projects
  drop column if exists issue_sync_enabled,
  drop column if exists issue_sync_last_pulled_at;
