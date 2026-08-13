-- 002_llm_settings: global LLM provider settings (US-1.14).
-- One config per org (unique org_id — Phase 3 multi-provider drops the
-- constraint). The API key is write-only: stored in Supabase Vault via
-- security definer RPCs; only key_last4 is ever readable by the client.

create table public.llm_settings (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null unique references public.organizations(id) on delete cascade,
  provider_type text not null check (provider_type in ('anthropic', 'openai', 'google', 'groq', 'ollama')),
  model text not null,
  base_url text,
  key_last4 text,
  vault_secret_id uuid,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.llm_settings enable row level security;

create policy "members manage their org llm settings"
  on public.llm_settings for all
  using (public.is_org_member(org_id))
  with check (public.is_org_member(org_id));

create or replace function public.touch_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create trigger llm_settings_touch
  before update on public.llm_settings
  for each row execute function public.touch_updated_at();

-- Write-only key storage. security definer (owner: postgres) so the
-- function can reach the vault schema; callers never can.
create or replace function public.set_llm_api_key(p_org uuid, p_key text)
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
  if p_key is null or length(p_key) < 4 then
    raise exception 'invalid key';
  end if;
  if not exists (select 1 from public.llm_settings where org_id = p_org) then
    raise exception 'save provider settings before setting a key';
  end if;

  select vault_secret_id into v_secret_id
  from public.llm_settings where org_id = p_org;

  if v_secret_id is null then
    v_secret_id := vault.create_secret(p_key, 'llm_api_key:' || p_org::text);
  else
    perform vault.update_secret(v_secret_id, p_key);
  end if;

  update public.llm_settings
  set vault_secret_id = v_secret_id,
      key_last4 = right(p_key, 4)
  where org_id = p_org;
end;
$$;

create or replace function public.clear_llm_api_key(p_org uuid)
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
  from public.llm_settings where org_id = p_org;

  if v_secret_id is not null then
    delete from vault.secrets where id = v_secret_id;
  end if;

  update public.llm_settings
  set vault_secret_id = null,
      key_last4 = null
  where org_id = p_org;
end;
$$;

revoke execute on function public.set_llm_api_key(uuid, text) from public, anon;
grant execute on function public.set_llm_api_key(uuid, text) to authenticated;
revoke execute on function public.clear_llm_api_key(uuid) from public, anon;
grant execute on function public.clear_llm_api_key(uuid) to authenticated;
