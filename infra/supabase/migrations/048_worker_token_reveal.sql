-- 048_worker_token_reveal: let an org member view a worker's full token
-- again after the initial mint (US-3.20). Deliberate change from us-3.1's
-- write-only-hash design: the plaintext now also lives in Vault (same
-- store as LLM provider keys / GitHub PATs), so a reveal RPC can return
-- it later. token_hash stays the source of truth for authenticating a
-- worker's requests — nothing about how a worker authenticates changes.

alter table public.workers add column vault_secret_id uuid;

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
  v_secret_id uuid;
begin
  if not public.is_org_member(p_org) then
    raise exception 'not authorized';
  end if;
  if p_name is null or length(trim(p_name)) = 0 then
    raise exception 'name required';
  end if;
  if p_type not in ('autonomous', 'human') then
    raise exception 'invalid worker type';
  end if;
  if p_user_id is not null and not exists (
    select 1 from public.organization_members m
    where m.org_id = p_org and m.user_id = p_user_id
  ) then
    raise exception 'linked user is not an org member';
  end if;

  v_token := 'sfw_' || encode(extensions.gen_random_bytes(24), 'hex');
  v_secret_id := vault.create_secret(v_token, 'worker_token:' || gen_random_uuid()::text);

  return query
  insert into public.workers
    (org_id, name, type, user_id, token_hash, token_last4, vault_secret_id)
  values (p_org, trim(p_name), p_type, p_user_id,
          encode(extensions.digest(v_token, 'sha256'), 'hex'),
          right(v_token, 4), v_secret_id)
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
  v_secret_id uuid;
  v_token text;
begin
  select org_id, vault_secret_id into v_org, v_secret_id
  from public.workers where id = p_worker;
  if v_org is null or not public.is_org_member(v_org) then
    raise exception 'not authorized';
  end if;

  v_token := 'sfw_' || encode(extensions.gen_random_bytes(24), 'hex');

  if v_secret_id is null then
    v_secret_id := vault.create_secret(v_token, 'worker_token:' || p_worker::text);
  else
    perform vault.update_secret(v_secret_id, v_token);
  end if;

  update public.workers
  set token_hash = encode(extensions.digest(v_token, 'sha256'), 'hex'),
      token_last4 = right(v_token, 4),
      vault_secret_id = v_secret_id,
      status = 'active'
  where id = p_worker;

  return v_token;
end;
$$;

-- Reveal the stored plaintext for an already-minted worker. Any member of
-- the worker's org may call this — same boundary as the existing
-- "members manage their org workers" RLS policy, just a new thing it
-- now permits.
create or replace function public.reveal_worker_token(p_worker uuid)
returns text
language plpgsql
security definer
set search_path = public
as $$
declare
  v_org uuid;
  v_secret_id uuid;
  v_token text;
begin
  select org_id, vault_secret_id into v_org, v_secret_id
  from public.workers where id = p_worker;
  if v_org is null or not public.is_org_member(v_org) then
    raise exception 'not authorized';
  end if;
  if v_secret_id is null then
    raise exception 'no stored token for this worker — regenerate to enable Show';
  end if;

  select decrypted_secret into v_token
  from vault.decrypted_secrets where id = v_secret_id;

  return v_token;
end;
$$;

revoke execute on function public.reveal_worker_token(uuid) from public, anon;
grant execute on function public.reveal_worker_token(uuid) to authenticated;
