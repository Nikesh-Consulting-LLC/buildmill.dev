-- US-83.2: a CLI-window session mints its own scoped gateway key.
--
-- Runs already meter cleanly: the key row carries run_id, the gateway stamps
-- llm_usage from it. A session had no scope at all — db.session_model_env was
-- a stub returning {"model": ...}, no key was ever minted, and the Phase 78
-- known gap stood: llm_usage.session_id existed with no writer, so a
-- session's calls landed with run_id AND session_id both null.
--
-- No RLS work: llm_gateway_keys is service-role-only (no client policies),
-- and this column changes nothing about who can read it.

alter table public.llm_gateway_keys
  add column if not exists session_id uuid
    references public.agent_sessions(id) on delete set null;

-- The gateway resolves a key by hash; this index serves the reverse walk —
-- "what did this session spend" — without scanning the key table.
create index if not exists llm_gateway_keys_session_idx
  on public.llm_gateway_keys (session_id)
  where session_id is not null;
