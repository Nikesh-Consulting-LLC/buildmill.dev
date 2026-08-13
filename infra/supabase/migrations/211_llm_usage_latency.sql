-- 211_llm_usage_latency: US-62.3 -- an LLM call says how long it took.
-- `llm_usage` (159) metered tokens and cost but never latency; there was no
-- way to attribute a slow run or a slow page to "the model was slow" versus
-- anything else. Timed at the gateway's one relay chokepoint
-- (llm_gateway.py's _meter_call), start-of-call to end-of-stream.

alter table public.llm_usage
  add column if not exists latency_ms int;
