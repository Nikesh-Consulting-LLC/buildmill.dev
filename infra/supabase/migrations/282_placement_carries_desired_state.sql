-- 282_placement_carries_desired_state (us-116.6): a queued pool placement
-- remembers the state the wizard asked for.
--
-- Slots come up paused by default (142): right for a slot with no grants and no
-- roles, which has nothing to claim. The wizard has just collected which
-- projects and which roles — grants and roles ARE the fail-closed gate — so it
-- asks for `enabled`, and an agent it creates is Ready on the roster the moment
-- its runner says hello, with no second click on another page.
--
-- An immediate placement carries that on the job's own context. A placement
-- that waits for the host's job lock (`agent_pool_placement_requests`,
-- US-57.3 follow-on) is replayed later by `pool_placement_sweep`, so the intent
-- has to be on the row.

alter table public.agent_pool_placement_requests
  add column if not exists desired_state text not null default 'paused'
    check (desired_state in ('paused', 'enabled'));

comment on column public.agent_pool_placement_requests.desired_state is
  'us-116.6: the state the placed slot lands in — enabled when the wizard '
  'asked for it (it collected grants and roles, the fail-closed gate), paused '
  'for the bare Add agent.';
