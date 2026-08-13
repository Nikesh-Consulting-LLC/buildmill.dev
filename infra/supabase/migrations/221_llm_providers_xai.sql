-- 221_llm_providers_xai: allow provider_type 'xai' on llm_providers.
--
-- Migration 045 renamed llm_settings to llm_providers but kept its original
-- check constraint (still named llm_settings_provider_type_check), whose
-- allowed list never grew past {anthropic, openai, google, groq, ollama}.
-- The backend has fully supported "xai" since the Grok Build module shipped
-- (llm_gateway.module_env, metering.OPENAI_SHAPED, runner_socket's
-- validate_model_provider_pairing all branch on it already) but no org could
-- ever save an xai-typed provider row -- the insert always violated this
-- constraint. Found while standing up a Grok Build agent end-to-end.

alter table public.llm_providers
  drop constraint llm_settings_provider_type_check,
  add constraint llm_providers_provider_type_check
    check (provider_type in ('anthropic', 'openai', 'google', 'groq', 'xai', 'ollama'));
