-- 040_worker_claims: runs join the worker pool (US-3.2).
--
-- No new status vocabulary: 'queued' IS the pool, 'running' IS claimed.
-- worker_id/claimed_at/claim_expires_at carry the lease; the reaper
-- returns expired claims to the pool instead of failing them.

alter table public.runs
  add column worker_id uuid references public.workers(id) on delete set null,
  add column claimed_at timestamptz,
  add column claim_expires_at timestamptz;

create index runs_pool_idx on public.runs (org_id, created_at)
  where status = 'queued';
create index runs_claim_expiry_idx on public.runs (claim_expires_at)
  where worker_id is not null;
