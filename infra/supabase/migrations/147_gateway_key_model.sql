-- 147: a gateway key remembers which model it was minted for (US-27.8).
--
-- An agent configured for `claude-sonnet-5` had its request answered by Groq,
-- which does not have that model, and the resulting error blamed the model:
--
--   "There's an issue with the selected model (claude-sonnet-5). It may not
--    exist or you may not have access to it."
--
-- while claude-sonnet-5 was demonstrably in the account's /v1/models list. The
-- manager spent an evening changing model ids that were never wrong (runs
-- 802506c9 and a7244f6c, 2026-07-26).
--
-- WHY: two routing tables decide different halves of one call and nothing
-- checks they agree.
--
--   runner_config.model_routes  (per agent, the runner console) decides the
--                               model NAME the CLI is told to use — it becomes
--                               ANTHROPIC_MODEL in the module's environment.
--   llm_function_routes         (per org, Settings → Routing) decides which
--                               PROVIDER the gateway forwards to.
--
-- `gateway.mint` sends `route = runner_<run kind>` (`runner_code`,
-- `runner_plan`, …). None of those are keys in LLM_FUNCTIONS — that registry
-- holds thinking functions (prd_draft, content_tldr, story_breakdown, …) and
-- the Routing UI renders it verbatim, so a run kind cannot be routed at all,
-- in the UI or in the table. With no route, `_targets_for` falls back to the
-- org's default provider and an Anthropic-shaped request goes wherever that
-- points.
--
-- THE FIX: the model implies the provider. A model id belongs to exactly one
-- configured provider — `llm_providers.models` already says which — so the
-- runner sends the model it was configured with when it mints its key, and the
-- gateway resolves the provider from that instead of from a route key with no
-- entry. The org default stays the fallback for thinking functions only;
-- nothing about the manager-facing Routing page changes.

alter table public.llm_gateway_keys add column if not exists model text;

comment on column public.llm_gateway_keys.model is
  'US-27.8: the model this key was minted for (runner_config.model_routes). '
  'The gateway resolves the provider from it — the provider whose `models` '
  'contains it — rather than from a route key that has no entry in '
  'LLM_FUNCTIONS. Null falls back to the pre-US-27.8 behaviour.';
