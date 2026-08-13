-- 011_run_change_metrics: lines added/removed, files touched, and a
-- frontend/backend/other split per run, parsed from the stored diff
-- (US-1.17). Nullable — null until computed (succeeded runs only, or
-- backfilled below); never a misleading 0.

alter table public.runs
  add column lines_added integer,
  add column lines_removed integer,
  add column files_changed integer,
  add column change_breakdown jsonb;
