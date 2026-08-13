-- 194_claude_subscription: the factory-held Claude subscription token (US-52.2).
-- One per org. The token is write-only: stored in Supabase Vault via security
-- definer RPCs (the migration-002 pattern) callable from the browser only; the
-- client reads at most a last-4 fingerprint and the expiry. The API reads the
-- secret server-side at claim time and hands it to a runner over its control
-- socket for subscription-mode Claude runs — no endpoint, response or log ever
-- echoes it.

create table public.claude_subscriptions (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null unique references public.organizations(id) on delete cascade,
  key_last4 text not null,
  vault_secret_id uuid not null,
  set_at timestamptz not null default now(),
  -- `claude setup-token` mints a one-year token; recorded so the settings card
  -- can warn before runs start failing on an expired credential.
  expires_at timestamptz not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.claude_subscriptions enable row level security;

-- Members see the fingerprint row; the secret itself lives in Vault, which no
-- policy here can reach. There is deliberately NO insert/update/delete policy:
-- writes go through the RPCs below, so the vault secret and the row can never
-- disagree.
create policy "members read their org claude subscription"
  on public.claude_subscriptions for select
  using (public.is_org_member(org_id));

create trigger claude_subscriptions_touch
  before update on public.claude_subscriptions
  for each row execute function public.touch_updated_at();

create or replace function public.set_claude_subscription_token(p_org uuid, p_token text)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_secret_id uuid;
begin
  if not public.is_org_member(p_org) then
    raise exception 'not authorized';
  end if;
  -- `claude setup-token` prints sk-ant-oat…; an API/gateway key pasted by
  -- mistake (sk-ant-api…) must be refused by shape, not stored under the
  -- wrong name to fail on a remote machine later.
  if p_token is null or p_token not like 'sk-ant-oat%' or length(p_token) < 20 then
    raise exception 'not a Claude subscription token — run `claude setup-token` and paste its sk-ant-oat… result';
  end if;

  select vault_secret_id into v_secret_id
  from public.claude_subscriptions where org_id = p_org;

  if v_secret_id is null then
    v_secret_id := vault.create_secret(p_token, 'claude_subscription_token:' || p_org::text);
    insert into public.claude_subscriptions (org_id, key_last4, vault_secret_id, set_at, expires_at)
    values (p_org, right(p_token, 4), v_secret_id, now(), now() + interval '1 year')
    on conflict (org_id) do update
      set key_last4 = excluded.key_last4,
          vault_secret_id = excluded.vault_secret_id,
          set_at = excluded.set_at,
          expires_at = excluded.expires_at;
  else
    perform vault.update_secret(v_secret_id, p_token);
    update public.claude_subscriptions
    set key_last4 = right(p_token, 4),
        set_at = now(),
        expires_at = now() + interval '1 year'
    where org_id = p_org;
  end if;
end;
$$;

create or replace function public.clear_claude_subscription_token(p_org uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_secret_id uuid;
begin
  if not public.is_org_member(p_org) then
    raise exception 'not authorized';
  end if;

  select vault_secret_id into v_secret_id
  from public.claude_subscriptions where org_id = p_org;

  if v_secret_id is not null then
    delete from vault.secrets where id = v_secret_id;
  end if;

  -- The row IS the token's presence — unlike llm_settings there is no other
  -- configuration living on it, so clearing means deleting.
  delete from public.claude_subscriptions where org_id = p_org;
end;
$$;

revoke execute on function public.set_claude_subscription_token(uuid, text) from public, anon;
grant execute on function public.set_claude_subscription_token(uuid, text) to authenticated;
revoke execute on function public.clear_claude_subscription_token(uuid) from public, anon;
grant execute on function public.clear_claude_subscription_token(uuid) to authenticated;
