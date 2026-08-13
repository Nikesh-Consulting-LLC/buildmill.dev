-- US-32.3: an agent runs one thing at a time.
--
-- `runner_config.concurrency` has been validated on every config PATCH, stored,
-- and pushed live over the runner socket since migration 100 — and the runner
-- has never read it. Grep `apps/runner` for it: no hits. A manager who set it to
-- 8 got the behaviour of 1, with nothing to say the dial did nothing.
--
-- It is also not the model wanted: an agent is an identity with its own service,
-- workspace and token, so more capacity means another agent — one click on the
-- agent server's page. Leaving the column behind as "harmless" leaves a
-- validated, stored, socket-pushed value that the next reader will reasonably
-- assume something honours.
--
-- This is a deletion, not a regression. Nothing loses a capability, because the
-- capability was never implemented.

alter table public.runner_config drop column if exists concurrency;

-- Slot templates carried the same dead key into every agent a host provisions.
update public.agent_servers
set slot_template = slot_template - 'concurrency'
where slot_template ? 'concurrency';
