-- 198_agent_enabled_kinds: what an agent does is a row of checkboxes
-- (US-53.4). Null means ALL kinds — every existing agent keeps behaving
-- exactly as today with no backfill. A stored list is the explicit choice
-- (an empty list is a deliberately benched agent); after the first save the
-- list is always explicit, so a kind added by a later migration arrives
-- unchecked and visible rather than silently granted.

alter table public.runner_config
  add column if not exists enabled_kinds jsonb;

comment on column public.runner_config.enabled_kinds is
  'US-53.4: the run kinds this agent claims (checkbox per kind). Null = all
   kinds (pre-53.4 behavior). [] = deliberately benched. Enforced runner-side
   beside module enablement; the config push carries it.';
