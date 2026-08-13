-- 200_agent_pools: a superadmin-owned machine becomes a named, capacity-
-- bounded pool that tenant orgs place agents on (US-57.1).
--
-- THE BLOCKER THIS MIGRATION REMOVES: agent_slots.org_id has been locked to
-- its host's org since 142_agent_servers.sql, not by convention but by a
-- composite foreign key — (agent_server_id, org_id) references
-- agent_servers(id, org_id) — and agent_server_jobs carries the same
-- composite shape twice over (to agent_servers, and to agent_slots). A slot
-- on a platform-owned host cannot belong to a tenant org until that
-- constraint is relaxed to a plain FK on agent_server_id/slot_id alone.
-- Existing rows are unaffected: every live slot's org_id already equals its
-- host's org_id (verified 2026-07-30), so this only widens what future rows
-- may do — it changes nothing about what already exists.
--
-- A shared machine IS a pool: no new table. agent_servers gains `shared`,
-- `pool_name`, `capacity`. RLS is untouched — the existing
-- "org members read agent servers" / "...agent slots" policies already read
-- the right thing once a slot's org_id means "whose agent this is" instead
-- of "whose machine this is": a tenant sees only its own slots, and only the
-- platform org (the new owner of a shared host) can see the host row itself.
-- The one new surface is `available_agent_pools()`, the tenant's only window
-- onto a shared machine — name and free count, never host, port, credential
-- or probe data.

-- ---------------------------------------------------------------------------
-- Composite FK -> plain FK surgery
-- ---------------------------------------------------------------------------
alter table public.agent_slots
  drop constraint agent_slots_agent_server_id_org_id_fkey;
alter table public.agent_slots
  add constraint agent_slots_agent_server_id_fkey
  foreign key (agent_server_id) references public.agent_servers (id) on delete cascade;

alter table public.agent_server_jobs
  drop constraint agent_server_jobs_agent_server_id_org_id_fkey;
alter table public.agent_server_jobs
  add constraint agent_server_jobs_agent_server_id_fkey
  foreign key (agent_server_id) references public.agent_servers (id) on delete cascade;

alter table public.agent_server_jobs
  drop constraint agent_server_jobs_slot_id_org_id_fkey;
alter table public.agent_server_jobs
  add constraint agent_server_jobs_slot_id_fkey
  foreign key (slot_id) references public.agent_slots (id) on delete set null;

comment on column public.agent_servers.org_id is
  'The org that OWNS the machine. For a shared platform pool this is the '
  'platform-admin org, not any tenant placing agents on it (US-57.1).';
comment on column public.agent_slots.org_id is
  'The tenant this agent belongs to — may differ from agent_servers.org_id '
  'on a shared platform-owned pool (US-57.1). On a single-tenant host the '
  'two still agree, as they always have.';

-- ---------------------------------------------------------------------------
-- Pool shape on agent_servers
-- ---------------------------------------------------------------------------
alter table public.agent_servers
  add column shared boolean not null default false,
  add column pool_name text,
  add column capacity int;

-- A shared machine must be named and sized; an unshared one carries neither.
-- `capacity` bounds agent COUNT (a superadmin decision, US-57.2's sibling on
-- the machine side) — distinct from the existing CPU/disk advisory in
-- agent_provision.MIN_FREE_GB_FOR_SLOT / _capacity_warning, which stays as
-- guidance shown only to the superadmin.
alter table public.agent_servers
  add constraint agent_servers_shared_pool_shape
  check (
    (not shared)
    or (pool_name is not null and capacity is not null and capacity between 0 and 64)
  );

create unique index agent_servers_pool_name_key
  on public.agent_servers (pool_name)
  where shared;

-- ---------------------------------------------------------------------------
-- The tenant's one window: name and free count, nothing else
-- ---------------------------------------------------------------------------
create or replace function public.available_agent_pools()
returns table (pool_id uuid, pool_name text, free_slots int)
language sql
security definer
set search_path = public
stable
as $$
  select
    a.id as pool_id,
    a.pool_name,
    greatest(a.capacity - coalesce(live.n, 0), 0)::int as free_slots
  from public.agent_servers a
  left join (
    select agent_server_id, count(*) as n
    from public.agent_slots
    where status = 'active'
    group by agent_server_id
  ) live on live.agent_server_id = a.id
  where a.shared = true and a.status = 'ready'
  order by free_slots desc, a.created_at asc;
$$;

comment on function public.available_agent_pools() is
  'Security-definer read for any authenticated tenant: ready shared pools by '
  'name and free-slot count only. Never returns host, port, credential, '
  'probe data, or another org''s occupancy (US-57.1).';

grant execute on function public.available_agent_pools() to authenticated;
