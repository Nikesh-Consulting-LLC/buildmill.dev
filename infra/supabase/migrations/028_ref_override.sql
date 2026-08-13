-- 028_ref_override: one-off deploy of a different ref (US-1.50).
-- Runs record when their payload came from an override rather than the
-- deployment's configured branch, so history and "currently deployed"
-- can badge them honestly.

alter table public.deployment_runs
  add column is_override boolean not null default false;
