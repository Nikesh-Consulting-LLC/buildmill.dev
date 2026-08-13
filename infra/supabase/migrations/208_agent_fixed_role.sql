-- 208_agent_fixed_role: agents get one fixed role, not a picker (US-61.1).
--
-- Role never gated an agent's own behavior: `has_org_capability` keys off
-- `pr.auth_user_id = auth.uid()`, and an agent principal's `auth_user_id`
-- is always null (086_unified_principals.sql) — it authenticates over a
-- worker token, a completely separate channel. Every place role actually
-- matters for an agent checks the CALLER'S (a human's) own capability to
-- create/place/configure it, never the agent's own membership row. What
-- gates what an agent can actually do is `worker_capabilities` +
-- `runner_config.enabled_kinds`, neither of which reference role at all.
--
-- So the six-role picker shown for an agent row was pure decoration that
-- could mislead a manager into thinking "Lead" vs "Developer" changed
-- something for an agent. This gives every agent one fixed value instead.

alter table public.organization_members drop constraint organization_members_role_check;
alter table public.organization_members
  add constraint organization_members_role_check
  check (role in ('owner', 'admin', 'lead', 'developer', 'reviewer', 'viewer', 'agent'));

alter table public.role_capabilities drop constraint role_capabilities_role_check;
alter table public.role_capabilities
  add constraint role_capabilities_role_check
  check (role in ('owner', 'admin', 'lead', 'developer', 'reviewer', 'viewer', 'agent'));

-- All false: the point is this role grants nothing, because nothing ever
-- checks it (see above) — an explicit empty row per capability rather than
-- an absent one, so the US-9.3 superadmin editor renders it like any other
-- role instead of a blank gap.
insert into public.role_capabilities (role, capability, allowed)
select 'agent', capability, false
from (
  values ('manage_org'), ('manage_members'), ('manage_project'),
         ('manage_work'), ('review_work'), ('develop'), ('view')
) as c(capability);

-- Converge every existing agent-kind member — their prior role (whatever
-- a human happened to pick) never granted them anything either.
update public.organization_members om
set role = 'agent'
from public.principals pr
where pr.id = om.principal_id and pr.kind = 'agent' and om.role <> 'agent';

-- create_worker (last redefined 202_org_agent_quota.sql): the autonomous
-- branch now mints role='agent' instead of 'developer' — the only other
-- change from that version is this one literal.
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
    (org_id, name, type, user_id, principal_id, token_hash, token_last4, vault_secret_id)
  values (p_org, trim(p_name), p_type, p_user_id, v_principal,
          encode(extensions.digest(v_token, 'sha256'), 'hex'),
          right(v_token, 4), v_secret_id)
  returning id, v_token;
end;
$$;
