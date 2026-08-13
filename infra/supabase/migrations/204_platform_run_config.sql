-- 204_platform_run_config: how an agent runs becomes the platform's to set,
-- inherited live by every agent in every org (US-57.6).
--
-- Two new tables:
--   platform_run_config — one row (a boolean PK forced to `true` is the
--     singleton trick), the model routes, autonomy policy and the three
--     limits the agent settings page today labels "Minutes per story",
--     "Max minutes per run" and "Attempts per work item".
--   agent_modules — the catalog: which modules (Claude Code, Grok Build,
--     OpenCode, the built-in Simulator) an org may even choose among. A
--     module marked unavailable disappears from new creation; an agent
--     already on it keeps running.
--
-- Enforcement rides `runner_config` itself, not a rewrite of every dispatch
-- read site: a BEFORE INSERT trigger seeds a fresh row's six platform-owned
-- columns from `platform_run_config`'s CURRENT values regardless of what an
-- insert statement supplied, and a BEFORE UPDATE trigger refuses any change
-- to those six columns unless it is the platform's own cascade (a
-- transaction-local session flag distinguishes the two — RLS cannot, since
-- `runner_config` writes always go through the API's service-role
-- connection, never PostgREST). `platform_run_config`'s own AFTER UPDATE
-- trigger cascades a superadmin's edit onto every existing agent
-- immediately, in the same transaction.
--
-- Deliberately NOT retroactive: this migration touches zero existing
-- `runner_config` rows. An agent's behavior is identical the moment before
-- and after this ships — divergent per-agent values already accumulated
-- over Phase 32/53 stay exactly as they are until the superadmin's first
-- edit to `platform_run_config`, which is the deliberate, singular moment
-- everyone converges. Forcing that convergence at migration time, before
-- any human decided the platform's values, would silently strip real
-- per-agent customization with no one having chosen the replacement.

create table public.platform_run_config (
  id boolean primary key default true check (id),
  autonomy_policy jsonb not null default '{}'::jsonb,
  model_routes jsonb not null default '{}'::jsonb,
  run_routes jsonb not null default '{}'::jsonb,
  max_run_minutes int,
  max_total_run_minutes int,
  max_item_attempts int not null default 3 check (max_item_attempts between 1 and 20),
  updated_at timestamptz not null default now()
);
insert into public.platform_run_config (id) values (true);

alter table public.platform_run_config enable row level security;
create policy "authenticated can read platform_run_config"
  on public.platform_run_config for select to authenticated using (true);
-- No client write policy: /admin writes it with the service role, after
-- require_platform_admin, exactly like role_capabilities and prompt templates.

create trigger platform_run_config_touch
  before update on public.platform_run_config
  for each row execute function public.touch_updated_at();

create table public.agent_modules (
  key text primary key,
  label text not null,
  available boolean not null default true
);
insert into public.agent_modules (key, label, available) values
  ('claude', 'Claude Code', true),
  ('grok', 'Grok Build', true),
  ('opencode', 'OpenCode', true),
  ('sim', 'Simulator', true);

alter table public.agent_modules enable row level security;
create policy "authenticated can read agent_modules"
  on public.agent_modules for select to authenticated using (true);

-- ---------------------------------------------------------------------------
-- runner_config: seed new rows from the platform config; refuse other writes
-- ---------------------------------------------------------------------------
create or replace function public.enforce_runner_config_platform_fields()
returns trigger
language plpgsql
as $$
declare
  p record;
begin
  if tg_op = 'INSERT' then
    select autonomy_policy, model_routes, run_routes, max_run_minutes,
           max_total_run_minutes, max_item_attempts
      into p
      from public.platform_run_config where id = true;
    new.autonomy_policy := p.autonomy_policy;
    new.model_routes := p.model_routes;
    new.run_routes := p.run_routes;
    new.max_run_minutes := p.max_run_minutes;
    new.max_total_run_minutes := p.max_total_run_minutes;
    new.max_item_attempts := p.max_item_attempts;
    return new;
  end if;

  if current_setting('buildmill.platform_cascade', true) = 'true' then
    return new;
  end if;
  if new.autonomy_policy is distinct from old.autonomy_policy
    or new.model_routes is distinct from old.model_routes
    or new.run_routes is distinct from old.run_routes
    or new.max_run_minutes is distinct from old.max_run_minutes
    or new.max_total_run_minutes is distinct from old.max_total_run_minutes
    or new.max_item_attempts is distinct from old.max_item_attempts
  then
    raise exception 'How an agent runs is the platform''s to set (US-57.6).';
  end if;
  return new;
end;
$$;

create trigger runner_config_platform_fields
  before insert or update on public.runner_config
  for each row execute function public.enforce_runner_config_platform_fields();

create or replace function public.cascade_platform_run_config()
returns trigger
language plpgsql
as $$
begin
  perform set_config('buildmill.platform_cascade', 'true', true);
  update public.runner_config set
    autonomy_policy = new.autonomy_policy,
    model_routes = new.model_routes,
    run_routes = new.run_routes,
    max_run_minutes = new.max_run_minutes,
    max_total_run_minutes = new.max_total_run_minutes,
    max_item_attempts = new.max_item_attempts;
  return new;
end;
$$;

create trigger platform_run_config_cascade
  after update on public.platform_run_config
  for each row execute function public.cascade_platform_run_config();
