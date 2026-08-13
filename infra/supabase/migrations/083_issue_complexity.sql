-- 083_issue_complexity: advisory complexity estimate per dispatchable work
-- item (US-7.1). Nullable columns (null = not scored). RLS unchanged: reads
-- ride the existing org-scoped issues policy; the columns are written only by
-- the API (best-effort, off the critical path).

alter table public.issues
  add column complexity text check (complexity in ('trivial', 'low', 'medium', 'high')),
  add column touches_critical boolean,
  add column data_model_impact text
    check (data_model_impact in ('none', 'backward_compatible', 'needs_migration')),
  add column complexity_rationale text,
  add column complexity_basis text check (complexity_basis in ('story', 'plan')),
  add column complexity_scored_at timestamptz,
  add column complexity_model text;
