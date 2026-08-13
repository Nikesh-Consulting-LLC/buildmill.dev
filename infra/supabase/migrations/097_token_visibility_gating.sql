-- 097_token_visibility_gating: a principal sees/rotates only its OWN token;
-- an admin (manage_members) sees/rotates every member's.
--
-- Before this, workers had a single is_org_member ALL policy and the reveal
-- RPC only checked membership — so any member could reveal/regenerate/revoke
-- anyone's token. Now: metadata stays org-readable (Live view + roster count),
-- but revealing/regenerating/revoking a token requires being its owner or
-- having manage_members. Also restores the Vault update in regenerate that
-- US-9.8's migration 091 dropped, so Show returns the current token after a
-- rotate.

-- A worker/token is "owned" by the caller when its principal is the caller's.
create or replace function public.is_own_principal(p_principal uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1 from public.principals
    where id = p_principal and auth_user_id = (select auth.uid())
  );
$$;
revoke execute on function public.is_own_principal(uuid) from public, anon;
grant execute on function public.is_own_principal(uuid) to authenticated;

-- Reveal: owner or manage_members.
create or replace function public.reveal_worker_token(p_worker uuid)
returns text
language plpgsql
security definer
set search_path = public
as $$
declare
  v_org uuid;
  v_secret_id uuid;
  v_principal uuid;
  v_token text;
begin
  select org_id, vault_secret_id, principal_id
    into v_org, v_secret_id, v_principal
  from public.workers where id = p_worker;
  if v_org is null then
    raise exception 'not authorized';
  end if;
  if not (public.is_own_principal(v_principal)
          or public.has_org_capability(v_org, 'manage_members')) then
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

-- Regenerate: owner or manage_members; keep the Vault secret in sync.
create or replace function public.regenerate_worker_token(p_worker uuid)
returns text
language plpgsql
security definer
set search_path = public
as $$
declare
  v_org uuid;
  v_secret_id uuid;
  v_principal uuid;
  v_token text;
begin
  select org_id, vault_secret_id, principal_id
    into v_org, v_secret_id, v_principal
  from public.workers where id = p_worker;
  if v_org is null then
    raise exception 'not authorized';
  end if;
  if not (public.is_own_principal(v_principal)
          or public.has_org_capability(v_org, 'manage_members')) then
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
revoke execute on function public.regenerate_worker_token(uuid) from public, anon;
grant execute on function public.regenerate_worker_token(uuid) to authenticated;

-- Workers RLS: read stays org-wide (Live + roster metadata); create/rotate/
-- revoke gated to the token's owner or a member manager.
drop policy if exists "members manage their org workers" on public.workers;

create policy "workers readable by org members"
  on public.workers for select
  using (public.is_org_member(org_id));

create policy "workers insert by org members"
  on public.workers for insert
  with check (public.is_org_member(org_id));

create policy "workers updated by owner or manager"
  on public.workers for update
  using (
    public.is_own_principal(principal_id)
    or public.has_org_capability(org_id, 'manage_members')
  )
  with check (
    public.is_own_principal(principal_id)
    or public.has_org_capability(org_id, 'manage_members')
  );

create policy "workers deleted by owner or manager"
  on public.workers for delete
  using (
    public.is_own_principal(principal_id)
    or public.has_org_capability(org_id, 'manage_members')
  );
