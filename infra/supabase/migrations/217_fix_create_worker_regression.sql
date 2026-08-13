-- 217_fix_create_worker_regression: hotfix for 216, which redefined
-- create_worker from a stale copy (039_workers.sql) instead of the
-- current one (last redefined in 208_agent_fixed_role.sql). That
-- regressed create_worker to drop: Vault token storage
-- (vault_secret_id), principal + organization_members creation for
-- both humans and agents, the org agent-quota check, and the
-- has_org_capability(p_org, 'develop') auth check — silently replaced
-- with a weaker is_org_member check.
--
-- This restores the full 208 body verbatim and adds only p_project on
-- top of it, exactly as 216 intended.

drop function if exists public.create_worker(uuid, text, text, uuid, uuid);

create or replace function public.create_worker(
  p_org uuid,
  p_name text,
  p_type text,
  p_user_id uuid default null,
  p_project uuid default null
)
returns table(worker_id uuid, token text)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_token text;
  v_principal uuid;
  v_secret_id uuid;
begin
  if not public.has_org_capability(p_org, 'develop') then
    raise exception 'not authorized';
  end if;
  if p_name is null or length(trim(p_name)) = 0 then
    raise exception 'name required';
  end if;
  if p_type not in ('autonomous', 'human') then
    raise exception 'invalid worker type';
  end if;
  if p_project is not null and not exists (
    select 1 from public.projects p where p.id = p_project and p.org_id = p_org
  ) then
    raise exception 'project does not belong to this org';
  end if;

  if p_type = 'human' then
    if p_user_id is not null then
      select id into v_principal from public.principals where auth_user_id = p_user_id;
    else
      select id into v_principal from public.principals where auth_user_id = (select auth.uid());
    end if;
    if v_principal is null then
      raise exception 'no principal for user';
    end if;
    if not exists (
      select 1 from public.organization_members
      where org_id = p_org and principal_id = v_principal
    ) then
      raise exception 'linked user is not an org member';
    end if;
  else
    if (
      select count(*)
      from public.organization_members om
      join public.principals pr on pr.id = om.principal_id
      where om.org_id = p_org and pr.kind = 'agent'
    ) >= (select max_agents from public.organizations where id = p_org) then
      raise exception 'This org has reached its agent limit.';
    end if;

    insert into public.principals (kind, display_name)
    values ('agent', trim(p_name))
    returning id into v_principal;
    insert into public.organization_members (org_id, principal_id, role)
    values (p_org, v_principal, 'agent');
  end if;

  v_token := 'sfw_' || encode(extensions.gen_random_bytes(24), 'hex');
  v_secret_id := vault.create_secret(v_token, 'worker_token:' || gen_random_uuid()::text);

  return query
  insert into public.workers
    (org_id, name, type, user_id, principal_id, project_id, token_hash, token_last4, vault_secret_id)
  values (p_org, trim(p_name), p_type, p_user_id, v_principal, p_project,
          encode(extensions.digest(v_token, 'sha256'), 'hex'),
          right(v_token, 4), v_secret_id)
  returning id, v_token;
end;
$$;

revoke execute on function public.create_worker(uuid, text, text, uuid, uuid) from public, anon;
grant execute on function public.create_worker(uuid, text, text, uuid, uuid) to authenticated;
