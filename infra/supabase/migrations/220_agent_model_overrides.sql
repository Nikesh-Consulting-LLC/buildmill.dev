-- US-66.1: an agent can pin its own model for a run kind.
--
-- A genuinely new column rather than a hole in us-57.6's lock: the trigger
-- `enforce_runner_config_platform_fields` (migration 204) only guards six
-- named columns (autonomy_policy, model_routes, run_routes, max_run_minutes,
-- max_total_run_minutes, max_item_attempts) -- it never references this one,
-- so a normal org write to it is naturally allowed with no change to that
-- trigger or to what it still locks down.
alter table public.runner_config
  add column model_overrides jsonb not null default '{}'::jsonb;

comment on column public.runner_config.model_overrides is
  'US-66.1: per-run-kind model id this agent pins, e.g. {"code": "llama-3.3-70b-versatile"}. '
  'Org-owned (same manage_work capability as the rest of this row) -- unlike the six '
  'columns enforce_runner_config_platform_fields guards, this one is not platform-only.';
