-- 281_live_runner_sessions (us-116.4): presence has an expiry, and one predicate.
--
-- Every surface defined "online" as `runner_sessions.disconnected_at is null`.
-- That column is written in exactly two places — the socket handler's `finally`
-- and supersede-on-reconnect — while `last_seen_at` is heartbeated every 30 s
-- and was read by nothing. Migration 099 justified its partial index by "the
-- reaper's job" and the reaper was never written; a hard-killed API left every
-- connected agent reading online for good.
--
-- This view IS the predicate: a session is live while its heartbeat is inside
-- the window (90 s = three missed beats, the same window claims already use for
-- a stale heartbeat). Every reader — the roster, the runner page, the machine
-- page, the wizard, the API — reads it, so nothing can define presence a second
-- way. The API's liveness loop also closes rows past the window
-- (`disconnected_at = now()`), so realtime subscribers on the table see the
-- change and the row itself agrees with the view; a heartbeat that arrives for
-- a swept row revives it (`disconnected_at = null`), so a false positive
-- self-heals on the next beat.
--
-- `security_invoker`: the view runs under the caller's RLS, so an org member
-- sees exactly the sessions the table's own policy already lets them see.

create or replace view public.live_runner_sessions
  with (security_invoker = true)
as
  select *
  from public.runner_sessions
  where disconnected_at is null
    and last_seen_at > now() - interval '90 seconds';

comment on view public.live_runner_sessions is
  'us-116.4: the ONE definition of a live runner session — connected and '
  'heartbeated inside the last 90 seconds. Read this, never the table''s '
  'disconnected_at, to answer "is this agent online".';

grant select on public.live_runner_sessions to authenticated;
grant select on public.live_runner_sessions to service_role;
