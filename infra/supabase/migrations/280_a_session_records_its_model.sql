-- 280_a_session_records_its_model (us-116.1): a CLI-window session says which
-- model it reasons with and which kind it resolved that through.
--
-- A session used to pick its model from `runner_config.model_overrides.code`
-- and stop — a two-line copy of the run resolver's rules that skipped every
-- other layer and hard-coded one kind, refusing an Architect with six roles
-- pinned for lacking a model for work it is configured never to do. It now
-- resolves through `run_settings.resolve` for a kind the agent actually
-- claims (`code` first when claimed, then ROUTE_KINDS order), and the answer
-- is recorded here so "why is this conversation using Sonnet" has an answer
-- that is not a guess. `model_kind` is the kind resolved through; the run
-- settings' own `sources` record says which layer supplied the model.
--
-- Nullable on purpose: a session refused before it resolves has neither.

alter table public.agent_sessions
  add column if not exists model text,
  add column if not exists model_kind text;

comment on column public.agent_sessions.model is
  'us-116.1: the model this session reasons with, as resolved at open through '
  'the run resolver — null when the session was refused before it resolved.';
comment on column public.agent_sessions.model_kind is
  'us-116.1: the run kind the session resolved its model through (code first '
  'when the agent claims it, else the first claimed kind in ROUTE_KINDS order '
  'that yields a model).';
