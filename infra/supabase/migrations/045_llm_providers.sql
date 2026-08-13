-- 045_llm_providers: multiple named LLM providers with per-function routing (US-3.17).
-- Reshapes llm_settings (single row per org, US-1.14) into llm_providers (many
-- named rows; exactly one default while any exist), adds llm_function_routes
-- mapping backend function keys to provider+model, and replaces the org-keyed
-- Vault RPCs with per-provider ones. Keys stay write-only.

alter table public.llm_settings rename to llm_providers;

alter table public.llm_providers
  drop constraint llm_settings_org_id_key,
  add column name text,
  add column models text[] not null default '{}',
  add column is_default boolean not null default false,
  add column default_model text;

-- Backfill: each org's legacy single config becomes its first named provider,
-- marked default, with its one model as both the list and the default.
update public.llm_providers
set name = initcap(provider_type),
    models = array[model],
    is_default = true,
    default_model = model;

alter table public.llm_providers
  alter column name set not null,
  drop column model,
  add constraint llm_providers_name_per_org unique (org_id, name),
  add constraint llm_providers_default_needs_model
    check (not is_default or default_model is not null),
  add constraint llm_providers_needs_models check (cardinality(models) > 0);

create unique index llm_providers_one_default_per_org
  on public.llm_providers (org_id) where is_default;

-- Routes: one row per (org, function). Deleting a provider drops its routes;
-- those functions fall back to the default at resolve time.
create table public.llm_function_routes (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  function_key text not null,
  provider_id uuid not null references public.llm_providers(id) on delete cascade,
  model text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (org_id, function_key)
);

alter table public.llm_function_routes enable row level security;

create policy "members manage their org llm routes"
  on public.llm_function_routes for all
  using (public.is_org_member(org_id))
  with check (public.is_org_member(org_id));

create trigger llm_function_routes_touch
  before update on public.llm_function_routes
  for each row execute function public.touch_updated_at();

-- Per-provider write-only key RPCs replace the org-keyed pair from 002.
drop function public.set_llm_api_key(uuid, text);
drop function public.clear_llm_api_key(uuid);

create or replace function public.set_llm_provider_key(p_provider uuid, p_key text)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_org uuid;
  v_secret_id uuid;
begin
  select org_id, vault_secret_id into v_org, v_secret_id
  from public.llm_providers where id = p_provider;
  if v_org is null or not public.is_org_member(v_org) then
    raise exception 'not authorized';
  end if;
  if p_key is null or length(p_key) < 4 then
    raise exception 'invalid key';
  end if;

  if v_secret_id is null then
    v_secret_id := vault.create_secret(p_key, 'llm_api_key:' || p_provider::text);
  else
    perform vault.update_secret(v_secret_id, p_key);
  end if;

  update public.llm_providers
  set vault_secret_id = v_secret_id,
      key_last4 = right(p_key, 4)
  where id = p_provider;
end;
$$;

create or replace function public.clear_llm_provider_key(p_provider uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_org uuid;
  v_secret_id uuid;
begin
  select org_id, vault_secret_id into v_org, v_secret_id
  from public.llm_providers where id = p_provider;
  if v_org is null or not public.is_org_member(v_org) then
    raise exception 'not authorized';
  end if;

  if v_secret_id is not null then
    delete from vault.secrets where id = v_secret_id;
  end if;

  update public.llm_providers
  set vault_secret_id = null,
      key_last4 = null
  where id = p_provider;
end;
$$;

revoke execute on function public.set_llm_provider_key(uuid, text) from public, anon;
grant execute on function public.set_llm_provider_key(uuid, text) to authenticated;
revoke execute on function public.clear_llm_provider_key(uuid) from public, anon;
grant execute on function public.clear_llm_provider_key(uuid) to authenticated;

-- Deleting a provider deletes its Vault secret (security definer trigger;
-- clients can never execute it directly — same hardening as migration 030).
create or replace function public.llm_provider_cleanup_secret()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if old.vault_secret_id is not null then
    delete from vault.secrets where id = old.vault_secret_id;
  end if;
  return old;
end;
$$;

revoke execute on function public.llm_provider_cleanup_secret() from public, anon, authenticated;

create trigger llm_providers_delete_secret
  before delete on public.llm_providers
  for each row execute function public.llm_provider_cleanup_secret();
