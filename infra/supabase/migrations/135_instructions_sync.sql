-- 135_instructions_sync: record what the repo's instruction files hold (US-22.7).
--
-- AGENTS.md / CLAUDE.md are only current if somebody pressed "Save
-- instructions". Edit a guideline section and the repo keeps yesterday's
-- instructions until a human remembers the button — and a coding agent gets
-- the repo as a zip pinned to a base_sha, so whatever was last committed is
-- what it obeys for the whole run.
--
-- These three columns let the pre-dispatch write be free when nothing has
-- changed: a hash of the assembled block, the commit that carried it, and
-- when. A content hash rather than a timestamp comparison, because it is the
-- only check that survives edits that cancel out, reordering that changes
-- nothing, and a failed write that must be retried.
--
-- The hash is deliberately NOT set on failure: leaving it stale is what makes
-- the next dispatch retry.

alter table public.projects
  add column if not exists instructions_synced_hash text,
  add column if not exists instructions_synced_sha text,
  add column if not exists instructions_synced_at timestamptz;

comment on column public.projects.instructions_synced_hash is
  'US-22.7: sha256 of the factory-owned instruction block as last successfully '
  'committed. Dispatch compares against this and makes no GitHub call when it '
  'matches.';

comment on column public.projects.instructions_synced_sha is
  'US-22.7: the commit that carried the last successful instruction write.';
