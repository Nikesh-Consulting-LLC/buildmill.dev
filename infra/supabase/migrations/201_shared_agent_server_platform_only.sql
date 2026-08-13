-- 201_shared_agent_server_platform_only: shared/pool_name/capacity are the
-- platform's to set, not any org's (US-57.1, closing a gap 200_agent_pools.sql
-- left open).
--
-- 143_agent_server_write_policies.sql grants UPDATE on agent_servers to any
-- org member with `manage_org` on their OWN org — correct for editing
-- workdir/modules/etc, but it would equally let an org owner PATCH their own
-- machine's `shared` column straight through PostgREST (this table is never
-- written through the service role) and self-declare their own hardware a
-- tenant-facing pool. RLS is row-level, not column-level, so it cannot say
-- "this column, only from the platform-admin org" — a trigger is the
-- enforcement point, and it applies regardless of which API route, or a raw
-- PostgREST call, attempts the write.

create or replace function public.enforce_shared_agent_server_is_platform_owned()
returns trigger
language plpgsql
as $$
begin
  if new.shared and not exists (
    select 1 from public.organizations
    where id = new.org_id and is_platform_admin
  ) then
    raise exception 'Only a platform-admin organization may mark an agent server shared.';
  end if;
  return new;
end;
$$;

create trigger agent_servers_shared_is_platform_owned
  before insert or update on public.agent_servers
  for each row execute function public.enforce_shared_agent_server_is_platform_owned();
