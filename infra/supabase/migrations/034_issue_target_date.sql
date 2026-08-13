-- 034_issue_target_date: optional target date for timeline / planning views (US-2.10).
alter table public.issues
  add column if not exists target_date date;

comment on column public.issues.target_date is
  'Optional calendar target for planning views; null means undated.';
