-- 148: re-issue a managed agent's token and push it to the machine (US-27.9).
--
-- Revoke is the right control for a hand-installed worker: someone pastes a
-- new token in. For a slot Build Mill installed there is nobody to paste it —
-- the token lives in a 0600 env file the app wrote over SFTP. Revoking one
-- leaves the machine running and useless: the control socket stays up (its
-- handshake already succeeded) and keeps heartbeating, while every HTTP pool
-- poll is rejected, silently, every three seconds. On 2026-07-26 that state
-- read as "waiting for work" on every surface for fourteen minutes.
--
-- So the repair gets its own job kind, and therefore its own row in the host's
-- Activity tab: mint a NEW token, write it to the slot's env file, restart the
-- service, and confirm the agent reconnected. Un-revoking is deliberately not
-- offered — a revoked credential stays revoked; the fix is a new one delivered
-- to the box. (The one-off repair on the night was only safe because the token
-- hash was untouched, and it is not a button anyone should have.)

alter table public.agent_server_jobs drop constraint if exists agent_server_jobs_kind_check;
alter table public.agent_server_jobs
  add constraint agent_server_jobs_kind_check
  check (kind in ('provision', 'add_slot', 'update', 'restart',
                  'remove_slot', 'teardown', 'probe', 'reissue_token'));
