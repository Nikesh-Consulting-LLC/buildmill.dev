-- 202_org_agent_quota: an org gets three agents by default; the superadmin
-- decides otherwise (US-57.2).
--
-- Enforcement lives inside create_worker itself — the same security-definer
-- RPC the add-agent wizard calls directly from the browser (no FastAPI layer
-- sits in front of it) — so a quota check anywhere else would be advisory
-- only. What counts: an agent-kind organization_members row for this org,
-- active or suspended alike (a suspended agent still occupies its place and
-- can come back); deleting the membership row (the roster's existing
-- "Remove member" action) is what frees it, immediately, because the count
-- is read live, not cached.

alter table public.organizations
  add column max_agents int not null default 3 check (max_agents between 1 and 100);

create or replace function public.create_worker(
  p_org uuid, p_name text, p_type text, p_user_id uuid default null
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
    -- US-57.2: the quota is read live against the roster, not a stored
    -- counter, so a Remove elsewhere in the same session is seen immediately.
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
    values (p_org, v_principal, 'developer');
  end if;

  v_token := 'sfw_' || encode(extensions.gen_random_bytes(24), 'hex');
  v_secret_id := vault.create_secret(v_token, 'worker_token:' || gen_random_uuid()::text);

  return query
  insert into public.workers
    (org_id, name, type, user_id, principal_id, token_hash, token_last4, vault_secret_id)
  values (p_org, trim(p_name), p_type, p_user_id, v_principal,
          encode(extensions.digest(v_token, 'sha256'), 'hex'),
          right(v_token, 4), v_secret_id)
  returning id, v_token;
end;
$$;
