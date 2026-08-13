-- 027_health_protected_audit: post-deploy health checks (US-1.40),
-- protected deployments (US-1.41), and the config audit trail (US-1.49).

-- ---------------------------------------------------------------------
-- US-1.40: optional per-deployment health check
-- ---------------------------------------------------------------------

alter table public.deployments
  add column health_check_url text not null default '',
  add column health_check_expected_status int not null default 200
    check (health_check_expected_status between 100 and 599),
  add column health_check_window_seconds int not null default 60
    check (health_check_window_seconds between 5 and 600),
  add column health_check_initial_delay_seconds int not null default 0
    check (health_check_initial_delay_seconds between 0 and 120);

-- ---------------------------------------------------------------------
-- US-1.41: protected deployments — owners only, enforced in RLS
-- ---------------------------------------------------------------------

alter table public.deployments
  add column protected boolean not null default false;

drop policy "members manage their org deployments" on public.deployments;

create policy "members read their org deployments"
  on public.deployments for select
  using (public.is_org_member(org_id));

create policy "members create deployments (protected needs owner)"
  on public.deployments for insert
  with check (
    public.is_org_member(org_id)
    and (not protected or public.is_org_owner(org_id))
  );

create policy "members update unprotected deployments"
  on public.deployments for update
  using (
    public.is_org_member(org_id)
    and (not protected or public.is_org_owner(org_id))
  )
  with check (
    public.is_org_member(org_id)
    and (not protected or public.is_org_owner(org_id))
  );

create policy "members delete unprotected deployments"
  on public.deployments for delete
  using (
    public.is_org_member(org_id)
    and (not protected or public.is_org_owner(org_id))
  );

-- ---------------------------------------------------------------------
-- US-1.49: append-only config audit trail
-- ---------------------------------------------------------------------

create table public.deployment_events (
  id bigint generated always as identity primary key,
  org_id uuid not null references public.organizations(id) on delete cascade,
  deployment_id uuid not null,
  actor text not null default '',
  event text not null check (event in ('created', 'updated', 'deleted')),
  areas jsonb not null default '[]'::jsonb,
  detail jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  foreign key (deployment_id, org_id)
    references public.deployments (id, org_id) on delete cascade
);

create index deployment_events_deployment_idx
  on public.deployment_events (deployment_id, id desc);
create index deployment_events_org_idx on public.deployment_events (org_id);

alter table public.deployment_events enable row level security;

-- SELECT only: append happens in security-definer triggers / api.
create policy "members read their org deployment events"
  on public.deployment_events for select
  using (public.is_org_member(org_id));

-- Config changes on the deployments row itself. Run bookkeeping columns
-- (staged zip metadata, current_run_id) are deliberately NOT config and
-- produce no event.
create or replace function public.log_deployment_config_change()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_areas jsonb := '[]'::jsonb;
  v_detail jsonb := '{}'::jsonb;
  v_actor text := coalesce(nullif(auth.jwt() ->> 'email', ''), 'api');
begin
  if tg_op = 'INSERT' then
    insert into public.deployment_events (org_id, deployment_id, actor, event, areas)
    values (new.org_id, new.id, v_actor, 'created', '["definition"]'::jsonb);
    return null;
  end if;

  if new.name is distinct from old.name
     or new.branch is distinct from old.branch
     or new.server_id is distinct from old.server_id
     or new.target_folder is distinct from old.target_folder
     or new.run_timeout_minutes is distinct from old.run_timeout_minutes then
    v_areas := v_areas || '["definition"]'::jsonb;
  end if;
  if new.script is distinct from old.script then
    v_areas := v_areas || '["script"]'::jsonb;
    -- Scripts are not secrets: keep the previous text so the trail
    -- answers "what did it run before" (US-1.49).
    v_detail := v_detail || jsonb_build_object('previous_script', old.script);
  end if;
  if new.source_folder is distinct from old.source_folder
     or new.exclude_patterns is distinct from old.exclude_patterns then
    v_areas := v_areas || '["source-filters"]'::jsonb;
  end if;
  if new.strategy is distinct from old.strategy
     or new.keep_releases is distinct from old.keep_releases then
    v_areas := v_areas || '["strategy"]'::jsonb;
  end if;
  if new.health_check_url is distinct from old.health_check_url
     or new.health_check_expected_status is distinct from old.health_check_expected_status
     or new.health_check_window_seconds is distinct from old.health_check_window_seconds
     or new.health_check_initial_delay_seconds is distinct from old.health_check_initial_delay_seconds then
    v_areas := v_areas || '["health-check"]'::jsonb;
  end if;
  if new.protected is distinct from old.protected then
    v_areas := v_areas || '["protection"]'::jsonb;
  end if;

  if v_areas = '[]'::jsonb then
    return null;
  end if;

  insert into public.deployment_events
    (org_id, deployment_id, actor, event, areas, detail)
  values (new.org_id, new.id, v_actor, 'updated', v_areas, v_detail);
  return null;
end;
$$;

create trigger deployments_config_audit
  after insert or update on public.deployments
  for each row execute function public.log_deployment_config_change();

-- Notification settings are SDK-writable — capture them by trigger too.
-- (Env var writes all flow through `api`, which records events itself
-- with the real actor.)
create or replace function public.log_deployment_notifications_change()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_row record;
  v_actor text := coalesce(nullif(auth.jwt() ->> 'email', ''), 'api');
begin
  if tg_op = 'DELETE' then
    v_row := old;
  else
    v_row := new;
  end if;
  -- A cascading deployment delete would otherwise insert an event for a
  -- vanishing parent and break the FK.
  if not exists (select 1 from public.deployments where id = v_row.deployment_id) then
    return null;
  end if;
  insert into public.deployment_events
    (org_id, deployment_id, actor, event, areas, detail)
  values (
    v_row.org_id, v_row.deployment_id, v_actor, 'updated',
    '["notifications"]'::jsonb,
    jsonb_build_object('events', v_row.events)
  );
  return null;
end;
$$;

create trigger deployment_notifications_audit
  after insert or update or delete on public.deployment_notifications
  for each row execute function public.log_deployment_notifications_change();
