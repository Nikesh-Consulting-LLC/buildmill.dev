-- 111_capability_matrix: US-13.10 — staff agents by stage.
--
-- The pipeline dispatches four run kinds but the us-3.12 allow-list
-- stored two booleans, with can_plan silently covering PRD drafting,
-- breakdown, and planning. Re-model as row-per-grant over seven named
-- stages: prd, breakdown, plan, code, plus the reserved test / release /
-- deploy — recorded now, enforced generically (capability = run kind)
-- the day those stages become dispatchable. Behavior-identical backfill:
-- can_plan → prd+breakdown+plan, can_code → code, so every worker can
-- claim exactly the set it could before. The two load-bearing semantics
-- are preserved verbatim: zero rows = unrestricted; the first row flips
-- the worker to allow-list mode. Git clone/fetch stays project-level
-- (any capability row for the project).

drop trigger if exists worker_capabilities_audit on public.worker_capabilities;

create table public.worker_capabilities_v2 (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  worker_id uuid not null,
  project_id uuid not null,
  capability text not null check (capability in
    ('prd', 'breakdown', 'plan', 'code', 'test', 'release', 'deploy')),
  created_at timestamptz not null default now(),
  unique (worker_id, project_id, capability),
  foreign key (worker_id, org_id)
    references public.workers (id, org_id) on delete cascade,
  foreign key (project_id, org_id)
    references public.projects (id, org_id) on delete cascade
);

insert into public.worker_capabilities_v2
  (org_id, worker_id, project_id, capability, created_at)
select wc.org_id, wc.worker_id, wc.project_id, cap.capability, wc.created_at
from public.worker_capabilities wc
cross join lateral (
  select unnest(
    case when wc.can_plan
      then array['prd', 'breakdown', 'plan'] else '{}'::text[] end
    || case when wc.can_code then array['code'] else '{}'::text[] end
  ) as capability
) cap;

drop table public.worker_capabilities;
alter table public.worker_capabilities_v2 rename to worker_capabilities;

create index worker_capabilities_org_idx
  on public.worker_capabilities (org_id);
create index worker_capabilities_project_idx
  on public.worker_capabilities (project_id);
create index worker_capabilities_worker_idx
  on public.worker_capabilities (worker_id);

alter table public.worker_capabilities enable row level security;

create policy "members manage their org worker capabilities"
  on public.worker_capabilities for all
  using (public.is_org_member(org_id))
  with check (public.is_org_member(org_id));

-- Audit v2: a row IS a grant, so the vocabulary is granted / revoked per
-- capability; 'updated' has nothing left to describe (the events table's
-- check constraint keeps it for historical rows).
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
  else
    v_row := new; v_event := 'granted';
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
      'capability', v_row.capability
    )
  );
  return null;
end;
$$;

create trigger worker_capabilities_audit
  after insert or delete on public.worker_capabilities
  for each row execute function public.log_worker_capability_change();
