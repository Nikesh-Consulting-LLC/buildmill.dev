-- 091_principal_tokens: per-principal router tokens (US-9.8).
--
-- Every worker token belongs to exactly one principal (workers.principal_id,
-- added in 086). A human self-manages personal tokens; an agent IS its token —
-- registering an autonomous worker mints a fresh agent principal + membership
-- in the same step. Creating a token now requires the `develop` capability, so
-- viewer/reviewer humans cannot push through the router. The runtime path
-- (get_worker_by_token: token_hash -> status -> org_id) is unchanged — principal_id
-- is additive.

create or replace function public.create_worker(
  p_org uuid,
  p_name text,
  p_type text,
  p_user_id uuid default null
)
returns table (worker_id uuid, token text)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_token text;
  v_principal uuid;
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
    -- Personal token owned by the caller (or an explicitly named member).
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
    -- Autonomous: mint a new agent principal + its org membership (developer).
    insert into public.principals (kind, display_name)
    values ('agent', trim(p_name))
    returning id into v_principal;
    insert into public.organization_members (org_id, principal_id, role)
    values (p_org, v_principal, 'developer');
  end if;

  v_token := 'sfw_' || encode(extensions.gen_random_bytes(24), 'hex');

  return query
  insert into public.workers (org_id, name, type, user_id, principal_id, token_hash, token_last4)
  values (p_org, trim(p_name), p_type, p_user_id, v_principal,
          encode(extensions.digest(v_token, 'sha256'), 'hex'),
          right(v_token, 4))
  returning id, v_token;
end;
$$;

create or replace function public.regenerate_worker_token(p_worker uuid)
returns text
language plpgsql
security definer
set search_path = public
as $$
declare
  v_org uuid;
  v_token text;
begin
  select org_id into v_org from public.workers where id = p_worker;
  if v_org is null or not public.has_org_capability(v_org, 'develop') then
    raise exception 'not authorized';
  end if;

  v_token := 'sfw_' || encode(extensions.gen_random_bytes(24), 'hex');

  update public.workers
  set token_hash = encode(extensions.digest(v_token, 'sha256'), 'hex'),
      token_last4 = right(v_token, 4),
      status = 'active'
  where id = p_worker;

  return v_token;
end;
$$;

revoke execute on function public.create_worker(uuid, text, text, uuid) from public, anon;
grant execute on function public.create_worker(uuid, text, text, uuid) to authenticated;
revoke execute on function public.regenerate_worker_token(uuid) from public, anon;
grant execute on function public.regenerate_worker_token(uuid) to authenticated;
