-- 231_agent_auto_repair: US-68.3 — a per-machine auto-repair service that
-- notices a degraded agent slot and works up an escalating ladder of fixes
-- (restart, re-issue its token, a full Update) on its own, instead of a
-- human having to notice and click "Restart". Enabled by default; a
-- platform admin can turn it off per machine.
--
-- Ladder state lives on the SLOT (agent_slots), because that is what a probe
-- actually finds broken (`service_state`); the toggle lives on the MACHINE
-- (agent_servers), because "auto repair on/off" is a per-host decision, not
-- a per-agent one.

alter table public.agent_servers
  add column if not exists auto_repair_enabled boolean not null default true;

alter table public.agent_slots
  add column if not exists auto_repair_attempts int not null default 0,
  add column if not exists auto_repair_last_at timestamptz,
  add column if not exists auto_repair_needs_attention boolean not null default false;

comment on column public.agent_servers.auto_repair_enabled is
  'When true, the liveness sweep may automatically restart, re-issue tokens '
  'for, or update this machine''s degraded agent slots (US-68.3). On by '
  'default; only a platform admin may turn it off (or back on).';

comment on column public.agent_slots.auto_repair_attempts is
  'How many rungs of the auto-repair ladder (restart, reissue_token, update) '
  'have been tried for this slot''s current degraded episode. Reset to 0 the '
  'moment a probe finds the slot active again.';

comment on column public.agent_slots.auto_repair_needs_attention is
  'True once the ladder is exhausted (3 attempts) without the slot '
  'recovering — the sweep stops touching it until a human intervenes or it '
  'recovers on its own.';
