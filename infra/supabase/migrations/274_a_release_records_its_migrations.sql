-- 274_a_release_records_its_migrations (us-101.5): "which migrations ran" gets
-- an answer that outlives the cut.
--
-- It is the first thing a reviewer asks and the last thing the system could
-- answer. The changed-file list exists for exactly one moment — inside the
-- cut, where US-82.3 already walks it to compute `touched_modules` — and was
-- thrown away immediately after. The release page had no way to show it, and
-- the agent could only find it by guessing this project's migration folder.
--
-- Recorded in the same best-effort block as touched_modules and with the same
-- rule: a first release, a missing token or a compare failure just leaves it
-- empty. Never a gate.

alter table public.releases
  add column if not exists migrations jsonb not null default '[]'::jsonb;

comment on column public.releases.migrations is
  'us-101.5: the migration files in this release''s commit range, recorded at '
  'the cut from the real compare — the only moment that list exists. Empty '
  'for a first release (no range to compare) and for a project whose '
  'migrations do not live in a folder named for them.';
