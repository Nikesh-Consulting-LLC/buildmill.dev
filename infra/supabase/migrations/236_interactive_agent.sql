-- 236_interactive_agent: the Buildmill Interactive Agent (US-78.3, US-78.6).
--
-- A third agent type: a fork of xai-org/grok-build driven over ACP, holding a
-- persistent session rather than running a one-shot command line. Two things
-- the database has to know about it — that it exists, and that it may only run
-- on a platform pool.

-- ---------------------------------------------------------------------------
-- US-78.3: the catalog entry the superadmin's run-config page toggles.
-- ---------------------------------------------------------------------------
insert into public.agent_modules (key, label, available) values
  ('interactive', 'Buildmill Interactive Agent', true)
on conflict (key) do nothing;

-- ---------------------------------------------------------------------------
-- US-78.6: interactive agents live on pools, never on an org's own machine.
--
-- Why a trigger and not a policy, for the second time in this table's life:
-- 143_agent_server_write_policies.sql grants UPDATE on agent_servers to any org
-- member with manage_org on their own org, and runner_config is written the
-- same way. RLS is row-level, so it cannot express "this value of this column,
-- only when that other table's row says shared" — 201 hit exactly this wall and
-- resolved it the same way. Enforcing only in the API would leave a raw
-- PostgREST call as the way around it.
--
-- The rule is checked from BOTH sides, because either write can create the
-- forbidden combination on its own: adding `interactive` to a worker whose slot
-- is on an owned machine, or moving a slot for an interactive worker onto one.
-- ---------------------------------------------------------------------------

create or replace function public.is_interactive_placement_legal(p_worker_id uuid)
returns boolean
language sql
stable
as $$
  -- True when this worker has no slot at all (not yet placed — the wizard
  -- patches config before placement, and refusing that ordering would refuse
  -- every legitimate creation), or when every slot it has is on a shared host.
  select not exists (
    select 1
    from public.agent_slots s
    join public.agent_servers h on h.id = s.agent_server_id
    where s.worker_id = p_worker_id
      and coalesce(h.shared, false) = false
  );
$$;

create or replace function public.enforce_interactive_is_pool_only()
returns trigger
language plpgsql
as $$
begin
  if new.enabled_modules @> array['interactive']::text[]
     and not public.is_interactive_placement_legal(new.worker_id) then
    raise exception
      'A Buildmill Interactive Agent runs on a platform agent pool only, not on an organization''s own machine.';
  end if;
  return new;
end;
$$;

create trigger runner_config_interactive_is_pool_only
  before insert or update on public.runner_config
  for each row execute function public.enforce_interactive_is_pool_only();

create or replace function public.enforce_slot_host_allows_its_modules()
returns trigger
language plpgsql
as $$
declare
  host_shared boolean;
  wants_interactive boolean;
begin
  if new.worker_id is null then
    return new;
  end if;
  select coalesce(shared, false) into host_shared
    from public.agent_servers where id = new.agent_server_id;
  select coalesce(enabled_modules, '{}') @> array['interactive']::text[]
    into wants_interactive
    from public.runner_config where worker_id = new.worker_id;
  if coalesce(wants_interactive, false) and not coalesce(host_shared, false) then
    raise exception
      'A Buildmill Interactive Agent runs on a platform agent pool only, not on an organization''s own machine.';
  end if;
  return new;
end;
$$;

create trigger agent_slots_host_allows_its_modules
  before insert or update on public.agent_slots
  for each row execute function public.enforce_slot_host_allows_its_modules();
