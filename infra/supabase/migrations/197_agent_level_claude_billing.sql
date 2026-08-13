-- 197_agent_level_claude_billing: billing becomes one switch on the agent
-- (US-53.1). Phase 52 made it a run setting riding the preset resolver —
-- nine presets to set, invisible until a runner declared it, and no surface
-- that answered "will this actually bill the subscription?". Whose money an
-- agent spends is a property of the agent, decided once.

alter table public.runner_config
  add column if not exists claude_billing text not null default 'api'
  check (claude_billing in ('api', 'subscription'));

comment on column public.runner_config.claude_billing is
  'US-53.1: how this agent''s Claude runs are billed — api (metered gateway)
   or subscription (Claude Code OAuth; off-meter). One home; presets and
   routes no longer carry it.';

-- The subtraction: strip the Phase 52 per-preset values. The nine presets
-- the 2026-07-30 UAT set come back clean; billing has exactly one home now.
update public.agent_presets set settings = settings - 'auth' where settings ? 'auth';
update public.preset_templates set settings = settings - 'auth' where settings ? 'auth';
