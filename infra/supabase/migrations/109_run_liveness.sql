-- US-13.6: unattended runs cannot stall silently.
--
-- last_heartbeat_at records the last time the claiming worker spoke —
-- stamped at claim and on every lease extension (every MCP tool call a
-- claim-holder makes extends the lease). "Silent but claimed" becomes
-- computable: a run whose worker last spoke long ago reads differently
-- from one heartbeating normally, without waiting for the lease (24h
-- for human-type workers) to expire. Cleared when the claim is released
-- or requeued.
--
-- Run-death events (claim-expired, run-released, run-failed) already
-- flow to the activity feed via the 081 view branches — no view change.

alter table public.runs
  add column if not exists last_heartbeat_at timestamptz;

update public.runs
set last_heartbeat_at = coalesce(claimed_at, started_at)
where worker_id is not null and last_heartbeat_at is null;

comment on column public.runs.last_heartbeat_at is
  'US-13.6: when the claiming worker last spoke (claim or any '
  'lease-extending call). Null when unclaimed.';
