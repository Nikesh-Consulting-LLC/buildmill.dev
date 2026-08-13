-- 206_buildmill_agent: Claude Code runs the platform pays for (US-60.1).
--
-- "Buildmill Agent" is a fourth catalog entry, identical to Claude Code under
-- the hood (same CLI, same npm package) but billed to ONE superadmin-owned
-- Anthropic key instead of the org's own. Reuses the existing gateway
-- (US-10.3) and metering (Phase 33) almost entirely:
--   - `runner_config.claude_billing` gains a third value, `platform`.
--   - `platform_llm_key` is a singleton (mirrors `platform_run_config`,
--     migration 204) holding one Vault-backed Anthropic key, write-only,
--     superadmin-only — mirrors `set_llm_provider_key`'s Vault mechanics
--     (migration 045) with `is_platform_admin()` in place of
--     `is_org_member()`, and no per-row org scoping since this key belongs
--     to no org.
--   - `llm_gateway_keys.platform_billed` is stamped at mint time from the
--     worker's `claude_billing`; the gateway (application code) branches on
--     it to resolve the platform's key instead of the org's own configured
--     provider, while usage still records the real org/worker/project.

insert into public.agent_modules (key, label, available)
values ('buildmill', 'Buildmill Agent', true);

alter table public.runner_config
  drop constraint runner_config_claude_billing_check,
  add constraint runner_config_claude_billing_check
    check (claude_billing in ('api', 'subscription', 'platform'));

create table public.platform_llm_key (
  id boolean primary key default true check (id),
  model text not null default 'claude-sonnet-5',
  vault_secret_id uuid,
  key_last4 text,
  updated_at timestamptz not null default now()
);
insert into public.platform_llm_key (id) values (true);

alter table public.platform_llm_key enable row level security;
create policy "authenticated can read platform_llm_key"
  on public.platform_llm_key for select to authenticated using (true);
-- key_last4/model only, never the Vault secret itself — same "at most a
-- fingerprint" rule as every other write-only credential in this app. No
-- client write policy: only the RPCs below (security definer) touch it.

create trigger platform_llm_key_touch
  before update on public.platform_llm_key
  for each row execute function public.touch_updated_at();

create or replace function public.set_platform_llm_key(p_key text)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_secret_id uuid;
begin
  if not public.is_platform_admin() then
    raise exception 'not authorized';
  end if;
  if p_key is null or length(p_key) < 4 then
    raise exception 'invalid key';
  end if;

  select vault_secret_id into v_secret_id from public.platform_llm_key where id = true;

  if v_secret_id is null then
    v_secret_id := vault.create_secret(p_key, 'platform_llm_key');
  else
    perform vault.update_secret(v_secret_id, p_key);
  end if;

  update public.platform_llm_key
  set vault_secret_id = v_secret_id, key_last4 = right(p_key, 4)
  where id = true;
end;
$$;

create or replace function public.clear_platform_llm_key()
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_secret_id uuid;
begin
  if not public.is_platform_admin() then
    raise exception 'not authorized';
  end if;
  select vault_secret_id into v_secret_id from public.platform_llm_key where id = true;
  if v_secret_id is not null then
    delete from vault.secrets where id = v_secret_id;
  end if;
  update public.platform_llm_key set vault_secret_id = null, key_last4 = null where id = true;
end;
$$;

revoke execute on function public.set_platform_llm_key(text) from public, anon;
grant execute on function public.set_platform_llm_key(text) to authenticated;
revoke execute on function public.clear_platform_llm_key() from public, anon;
grant execute on function public.clear_platform_llm_key() to authenticated;

alter table public.llm_gateway_keys
  add column platform_billed boolean not null default false;
