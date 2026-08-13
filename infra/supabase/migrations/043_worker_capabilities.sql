-- 043_worker_capabilities: US-3.12 — per-project allow-lists for workers.
-- Default is unrestricted: zero rows for a worker = it may serve every
-- project and both kinds. Its first row flips the worker to allow-list
-- mode. Enforced in the worker pool/claim layer and the git proxy
-- (clone/fetch); an existing claim is never revoked retroactively.

-- Composite uniques so capability rows can't reference another org's
-- worker or project — a cross-org write would otherwise pass this
-- table's own org-scoped RLS.
alter table public.workers
  add constraint workers_id_org_key unique (id, org_id);
alter table public.projects
  add constraint projects_id_org_key unique (id, org_id);

create table public.worker_capabilities (
  worker_id uuid not null,
  project_id uuid not null,
  org_id uuid not null references public.organizations(id) on delete cascade,
  can_plan boolean not null default true,
  can_code boolean not null default true,
  created_at timestamptz not null default now(),
  primary key (worker_id, project_id),
  check (can_plan or can_code),
  foreign key (worker_id, org_id)
    references public.workers (id, org_id) on delete cascade,
  foreign key (project_id, org_id)
    references public.projects (id, org_id) on delete cascade
);

create index worker_capabilities_org_idx
  on public.worker_capabilities (org_id);
create index worker_capabilities_project_idx
  on public.worker_capabilities (project_id);

alter table public.worker_capabilities enable row level security;

create policy "members manage their org worker capabilities"
  on public.worker_capabilities for all
  using (public.is_org_member(org_id))
  with check (public.is_org_member(org_id));

-- Append-only audit trail (us-1.49 pattern): who changed what, when.
create table public.worker_capability_events (
  id bigint generated always as identity primary key,
  org_id uuid not null references public.organizations(id) on delete cascade,
  worker_id uuid not null references public.workers(id) on delete cascade,
  actor text not null default '',
  event text not null check (event in ('granted', 'updated', 'revoked')),
  detail jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index worker_capability_events_worker_idx
  on public.worker_capability_events (worker_id, id desc);
create index worker_capability_events_org_idx
  on public.worker_capability_events (org_id);

alter table public.worker_capability_events enable row level security;

-- SELECT only: append happens in the security-definer trigger.
create policy "members read their org worker capability events"
  on public.worker_capability_events for select
  using (public.is_org_member(org_id));

create or replace function public.log_worker_capability_change()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_row record;
  v_event text;
  v_actor text := coalesce(nullif(auth.jwt() ->> 'email', ''), 'api');
begin
  if tg_op = 'DELETE' then
    v_row := old; v_event := 'revoked';
  elsif tg_op = 'INSERT' then
    v_row := new; v_event := 'granted';
  else
    v_row := new; v_event := 'updated';
  end if;
  -- A cascading worker delete would otherwise insert an event for a
  -- vanishing parent and break the FK.
  if not exists (select 1 from public.workers where id = v_row.worker_id) then
    return null;
  end if;
  insert into public.worker_capability_events
    (org_id, worker_id, actor, event, detail)
  values (
    v_row.org_id, v_row.worker_id, v_actor, v_event,
    jsonb_build_object(
      'project_id', v_row.project_id,
      'can_plan', v_row.can_plan,
      'can_code', v_row.can_code
    )
  );
  return null;
end;
$$;

create trigger worker_capabilities_audit
  after insert or update or delete on public.worker_capabilities
  for each row execute function public.log_worker_capability_change();
