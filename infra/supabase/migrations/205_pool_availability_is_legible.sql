-- 205_pool_availability_is_legible: a tenant can tell WHY there is no pool
-- (US-57.10).
--
-- `available_agent_pools()` filtered on `status = 'ready'`, so three different
-- situations arrived at the wizard as the same empty list: no shared pool
-- exists, a pool exists but is not ready, and every ready pool is full. The
-- wizard had one sentence for all three — "No pool has room right now, ask the
-- superadmin to provision or resize one" — and on 2026-07-31 it said that
-- about a pool with 31 of 32 slots free that was sitting at `status = 'error'`.
-- Provisioning or resizing would have produced another row the same filter
-- rejected.
--
-- So the function stops filtering and starts reporting: the caller gets
-- `status` and decides for itself. The disclosure is deliberately coarse —
-- `status` is one of six fixed words. Host, port, credential, `probe_error`
-- and per-org occupancy are still never returned; a tenant needs to know that
-- a pool is unavailable, not the shape of the platform's failure.
--
-- The return type changes, and `create or replace` cannot change a function's
-- OUT columns, so this drops and recreates (and re-issues the grant).

drop function if exists public.available_agent_pools();

create function public.available_agent_pools()
returns table (pool_id uuid, pool_name text, status text, free_slots int)
language sql
security definer
set search_path = public
stable
as $$
  select
    a.id as pool_id,
    a.pool_name,
    a.status,
    greatest(a.capacity - coalesce(live.n, 0), 0)::int as free_slots
  from public.agent_servers a
  left join (
    select agent_server_id, count(*) as n
    from public.agent_slots
    where status = 'active'
    group by agent_server_id
  ) live on live.agent_server_id = a.id
  where a.shared = true and a.status <> 'removed'
  order by (a.status = 'ready') desc, free_slots desc, a.created_at asc;
$$;

comment on function public.available_agent_pools() is
  'Security-definer read for any authenticated tenant: shared pools by name, '
  'coarse status and free-slot count only. US-57.10 added `status` and dropped '
  'the ready-only filter so the caller can tell "no pool exists" from "no pool '
  'is ready" from "every pool is full" — a filtered empty list cannot. Still '
  'never returns host, port, credential, probe data (`probe_error` in '
  'particular), or another org''s occupancy (US-57.1). Placement remains '
  'ready-and-has-room only; this read makes the refusal legible, not the '
  'button more permissive.';

grant execute on function public.available_agent_pools() to authenticated;
