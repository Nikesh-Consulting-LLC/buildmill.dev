-- 041_push_log: factory-remote push log on runs (US-3.8).
--
-- Every successful push through the factory git remote records the
-- branch head here — the raw material for push-detection hand-back
-- (US-3.4) and the proxy's history-rewrite check.

alter table public.runs
  add column pushed_head_sha text,
  add column pushed_at timestamptz;
