-- 149_max_run_minutes: an agent gets the time its work needs (US-31.2).
--
-- The autonomous claim lease was a module constant — 15 minutes for every
-- agent, whatever it was asked to do — and the agent's own CLI limit (1200s)
-- was LARGER than the lease, so a slow run could lose its claim mid-thought
-- while a dead one parked a run for the full quarter hour. This column makes
-- the lease the manager's number: per worker, nullable, null = the existing
-- default for the worker type. `claim_run` and `extend_claim` read it; the
-- work-context bundle carries the resulting lease so the runner derives its
-- CLI timeout strictly BELOW it.
--
-- The staleness half of US-31.2 needs no schema: `last_heartbeat_at` has
-- existed since 039 — written on every beat and, until now, read by nothing.

alter table public.runner_config
  add column max_run_minutes int
  check (max_run_minutes is null or max_run_minutes between 1 and 1440);

comment on column public.runner_config.max_run_minutes is
  'US-31.2: how long this agent may hold one run (minutes, 1–1440). Null = '
  'the default lease for the worker type (15 minutes autonomous / 24h human). '
  'The runner derives its CLI timeout from this, strictly below it.';
