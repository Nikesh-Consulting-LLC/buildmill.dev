-- 096_default_principal_token: every principal has a router token attached.
--
-- US-9.16: a human member gets a token minted automatically (so "each user
-- has a token" without a create step), agents already get theirs from
-- create_worker, and an admin can regenerate anyone's from the Team page.
--
-- Also fixes a regression: US-9.8's create_worker (091) dropped the Vault
-- secret that US-3.20 (048) added, so freshly minted tokens couldn't be
-- revealed via Show until regenerated. Restored here for both the RPC and
-- the auto-mint path, so a revealed token is always available.

-- 1) create_worker: keep the 091 principal logic, restore Vault storage.
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

revoke execute on function public.create_worker(uuid, text, text, uuid) from public, anon;
grant execute on function public.create_worker(uuid, text, text, uuid) to authenticated;

-- 2) Auto-mint a default token for every new human member. Agents get theirs
-- from create_worker (which inserts membership for an 'agent' principal, so
-- this trigger skips them). Idempotent: skips if the principal already has a
-- worker in the org.
create or replace function public.mint_default_token_for_member()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_kind text;
  v_auth uuid;
  v_token text;
  v_secret_id uuid;
begin
  select kind, auth_user_id into v_kind, v_auth
  from public.principals where id = NEW.principal_id;

  if v_kind is distinct from 'human' then
    return NEW;
  end if;
  if exists (
    select 1 from public.workers
    where org_id = NEW.org_id and principal_id = NEW.principal_id
  ) then
    return NEW;
  end if;

  v_token := 'sfw_' || encode(extensions.gen_random_bytes(24), 'hex');
  v_secret_id := vault.create_secret(v_token, 'worker_token:' || gen_random_uuid()::text);

  insert into public.workers
    (org_id, name, type, user_id, principal_id, token_hash, token_last4, vault_secret_id)
  values (NEW.org_id, 'Access token', 'human', v_auth, NEW.principal_id,
          encode(extensions.digest(v_token, 'sha256'), 'hex'),
          right(v_token, 4), v_secret_id);

  return NEW;
end;
$$;

drop trigger if exists trg_mint_default_token on public.organization_members;
create trigger trg_mint_default_token
  after insert on public.organization_members
  for each row execute function public.mint_default_token_for_member();

-- 3) Backfill: give every current tokenless human member a token.
do $$
declare
  r record;
  v_token text;
  v_secret_id uuid;
begin
  for r in
    select m.org_id, m.principal_id, p.auth_user_id as auth
    from public.organization_members m
    join public.principals p on p.id = m.principal_id
    where p.kind = 'human'
      and not exists (
        select 1 from public.workers w
        where w.org_id = m.org_id and w.principal_id = m.principal_id
      )
  loop
    v_token := 'sfw_' || encode(extensions.gen_random_bytes(24), 'hex');
    v_secret_id := vault.create_secret(v_token, 'worker_token:' || gen_random_uuid()::text);
    insert into public.workers
      (org_id, name, type, user_id, principal_id, token_hash, token_last4, vault_secret_id)
    values (r.org_id, 'Access token', 'human', r.auth, r.principal_id,
            encode(extensions.digest(v_token, 'sha256'), 'hex'),
            right(v_token, 4), v_secret_id);
  end loop;
end $$;
