-- 074_epics_numbering: epics become the numbering root (US-7.10).
--
-- Adds a per-project epic number, exactly-one-active-epic, a Build-Mill-native
-- work-item id (epic-scoped, type-prefixed, derived in code from item_no/
-- sub_no), a server-side start-new-epic gate, mandatory epic linkage, and an
-- activity-feed branch for epic open/close. Numbers are assigned atomically at
-- creation and are immutable thereafter.

-- ---------------------------------------------------------------- epics cols
alter table public.epics
  add column number int,
  add column active boolean not null default false,
  add column created_by uuid,
  add column completed_at timestamptz,
  add column completed_by uuid;

-- An active epic must be open (same-row check).
alter table public.epics
  add constraint epics_active_open check (not active or status = 'open');

-- ------------------------------------------------------- backfill: numbers
-- Every existing epic gets a per-project number by created_at.
with ranked as (
  select id,
         row_number() over (partition by project_id order by created_at, id) as rn
  from public.epics
)
update public.epics e set number = ranked.rn
from ranked where ranked.id = e.id;

-- ------------------------------------- backfill: one active epic per project
-- Projects with no epics get an Epic 1. Projects with epics activate the
-- highest-numbered OPEN epic; if every epic is completed, mint a fresh one.
do $$
declare
  r record;
  v_open uuid;
  v_max int;
begin
  for r in select id, org_id from public.projects loop
    -- already has an active epic? nothing to do.
    if exists (select 1 from public.epics where project_id = r.id and active) then
      continue;
    end if;
    select id into v_open from public.epics
      where project_id = r.id and status = 'open'
      order by number desc limit 1;
    if v_open is not null then
      update public.epics set active = true where id = v_open;
    else
      select coalesce(max(number), 0) into v_max
        from public.epics where project_id = r.id;
      insert into public.epics
        (org_id, project_id, title, status, active, number)
      values (r.org_id, r.id, 'Epic ' || (v_max + 1), 'open', true, v_max + 1);
    end if;
  end loop;
end $$;

alter table public.epics alter column number set not null;
alter table public.epics
  add constraint epics_project_number_unique unique (project_id, number);
create unique index epics_one_active_per_project
  on public.epics (project_id) where active;

-- ------------------------------------------------- epic number assignment
-- New epics (e.g. us-2.8 manual creation) get the next per-project number
-- under an advisory lock so concurrent creates never collide.
create or replace function public.assign_epic_number()
returns trigger language plpgsql as $$
begin
  if new.number is null then
    perform pg_advisory_xact_lock(hashtext('epic-number:' || new.project_id::text));
    select coalesce(max(number), 0) + 1 into new.number
      from public.epics where project_id = new.project_id;
  end if;
  return new;
end;
$$;

create trigger epics_assign_number
  before insert on public.epics
  for each row execute function public.assign_epic_number();

-- --------------------------------------------- seed Epic 1 on new projects
create or replace function public.seed_project_epic()
returns trigger language plpgsql as $$
begin
  insert into public.epics (org_id, project_id, title, status, active, number)
  values (new.org_id, new.id, 'Epic 1', 'open', true, 1);
  return new;
end;
$$;

create trigger projects_seed_epic
  after insert on public.projects
  for each row execute function public.seed_project_epic();

-- ----------------------------------------------------------- issue numbers
alter table public.issues
  add column item_no int,
  add column sub_no int;

-- Backfill: assign epic-less issues to their project's Epic 1, then inherit
-- children into their parent's epic, then number.
update public.issues i
set epic_id = e.id
from public.epics e
where i.epic_id is null and e.project_id = i.project_id and e.number = 1;

update public.issues c
set epic_id = p.epic_id
from public.issues p
where c.parent_id = p.id and c.parent_id is not null;

-- Top-level items: a single per-epic sequence by created_at.
with tl as (
  select id,
         row_number() over (partition by epic_id order by created_at, id) as rn
  from public.issues where parent_id is null
)
update public.issues i set item_no = tl.rn from tl where tl.id = i.id;

-- Stories under a feature: item_no = parent's item_no, sub_no per parent.
with ch as (
  select c.id, p.item_no as parent_item,
         row_number() over (partition by c.parent_id order by c.created_at, c.id) as rn
  from public.issues c
  join public.issues p on p.id = c.parent_id
)
update public.issues i set item_no = ch.parent_item, sub_no = ch.rn
from ch where ch.id = i.id;

alter table public.issues alter column epic_id set not null;

-- Assign item_no/sub_no atomically at creation; default/inherit the epic.
-- item_no is set once and never changes (re-parenting does not renumber).
create or replace function public.assign_issue_number()
returns trigger language plpgsql as $$
declare
  v_active uuid;
  v_parent record;
begin
  if new.item_no is not null then
    return new;  -- explicit/backfilled — immutable
  end if;
  if new.parent_id is not null then
    select epic_id, item_no into v_parent
      from public.issues where id = new.parent_id;
    if v_parent.epic_id is null then
      raise exception 'parent issue has no epic';
    end if;
    new.epic_id := v_parent.epic_id;  -- a story inherits its feature's epic
    perform pg_advisory_xact_lock(hashtext('issue-child:' || new.parent_id::text));
    new.item_no := v_parent.item_no;
    select coalesce(max(sub_no), 0) + 1 into new.sub_no
      from public.issues where parent_id = new.parent_id;
  else
    if new.epic_id is null then
      select id into v_active
        from public.epics where project_id = new.project_id and active limit 1;
      if v_active is null then
        raise exception 'project % has no active epic', new.project_id;
      end if;
      new.epic_id := v_active;
    end if;
    perform pg_advisory_xact_lock(hashtext('issue-epic:' || new.epic_id::text));
    select coalesce(max(item_no), 0) + 1 into new.item_no
      from public.issues where epic_id = new.epic_id and parent_id is null;
    new.sub_no := null;
  end if;
  return new;
end;
$$;

create trigger issues_assign_number
  before insert on public.issues
  for each row execute function public.assign_issue_number();

-- ----------------------------------------------- start-new-epic gate (RPC)
-- Enforced in the DB, not just the UI. A completion backstop trigger blocks
-- closing an epic while open work remains, whatever the write path.
create or replace function public.guard_epic_completion()
returns trigger language plpgsql as $$
declare v_open int;
begin
  if new.status = 'completed' and old.status <> 'completed' then
    select count(*) into v_open from public.issues
      where epic_id = new.id and abandoned_at is null
        and status in ('draft', 'prd-review', 'ready', 'planning',
          'plan-review', 'planned', 'queued', 'running', 'needs-fixes',
          'in-review', 'failed');
    if v_open > 0 then
      raise exception
        'cannot complete Epic %: % open work item(s) remain', new.number, v_open;
    end if;
  end if;
  return new;
end;
$$;

create trigger epics_guard_completion
  before update on public.epics
  for each row execute function public.guard_epic_completion();

create or replace function public.start_new_epic(p_project uuid)
returns public.epics language plpgsql as $$
declare
  v_org uuid;
  v_active public.epics%rowtype;
  v_new public.epics%rowtype;
begin
  select org_id into v_org from public.projects where id = p_project;
  if v_org is null then
    raise exception 'project not found';
  end if;
  select * into v_active
    from public.epics where project_id = p_project and active for update;
  if not found then
    raise exception 'project has no active epic';
  end if;

  -- Close the current epic (the completion trigger enforces the no-open gate).
  update public.epics
    set active = false, status = 'completed',
        completed_at = now(), completed_by = auth.uid()
    where id = v_active.id;

  insert into public.epics
    (org_id, project_id, title, status, active, number, created_by)
  values
    (v_org, p_project, 'Epic ' || (v_active.number + 1), 'open', true,
     v_active.number + 1, auth.uid())
  returning * into v_new;
  return v_new;
end;
$$;

revoke execute on function public.start_new_epic(uuid) from public, anon;
grant execute on function public.start_new_epic(uuid) to authenticated;

-- ----------------------------------------------- activity feed: epic branch
-- Re-declare the view (070) with an epics-derived branch. No new writes —
-- opened/completed are derived from the epics rows themselves.
create or replace view public.activity_feed
with (security_invoker = true)
as
select
  'approval:' || a.id as id, a.org_id, i.project_id, p.name as project_name,
  'gate' as kind, a.gate || ' ' || a.decision as action,
  'issue' as object_type, a.issue_id as object_id, i.title as object_label,
  'user' as actor_type, a.actor as actor_id, '' as actor_name,
  'success' as outcome, jsonb_build_object('comment', a.comment) as detail,
  a.created_at
from public.approvals a
join public.issues i on i.id = a.issue_id
join public.projects p on p.id = i.project_id
union all
select
  'event:' || e.id, e.org_id, i.project_id, p.name,
  'issue', replace(e.type, '-', ' '),
  'issue', e.issue_id, i.title,
  case
    when e.payload ? 'worker' or e.payload ->> 'author_kind' = 'worker'
      then 'worker'
    when e.payload ? 'author' then 'user'
    else 'system'
  end,
  null::uuid, coalesce(e.payload ->> 'worker', e.payload ->> 'author', ''),
  case when e.type in ('claim-expired') then 'failure' else 'success' end,
  e.payload, e.created_at
from public.issue_events e
join public.issues i on i.id = e.issue_id
join public.projects p on p.id = i.project_id
where e.type not in ('progress-note', 'run-failed')
union all
select
  'run:' || r.id || ':queued', r.org_id, i.project_id, p.name,
  'run', r.kind || ' run dispatched',
  'issue', i.id, i.title,
  'system', null::uuid, 'factory', 'success', '{}'::jsonb, r.created_at
from public.runs r
join public.issues i on i.id = r.issue_id
join public.projects p on p.id = i.project_id
union all
select
  'run:' || r.id || ':finished', r.org_id, i.project_id, p.name,
  'run',
  case
    when r.status = 'succeeded' and r.pr_url is not null
      then r.kind || ' run submitted — PR opened'
    when r.status = 'succeeded' then r.kind || ' run submitted'
    else r.kind || ' run failed'
  end,
  'issue', i.id, i.title,
  'worker', null::uuid, coalesce(w.name, ''),
  case when r.status = 'failed' then 'failure' else 'success' end,
  case
    when r.status = 'failed' then jsonb_build_object(
      'error', r.error, 'stdout_tail', right(r.stdout, 1500))
    else jsonb_build_object('pr_url', r.pr_url)
  end,
  r.finished_at
from public.runs r
join public.issues i on i.id = r.issue_id
join public.projects p on p.id = i.project_id
left join public.workers w on w.id = r.worker_id
where r.finished_at is not null
union all
select
  'deploy:' || dr.id || ':started', dr.org_id, d.project_id, p.name,
  'deploy', 'deployment started (' || d.name || ')',
  'deployment', dr.deployment_id, d.name,
  case when dr.started_by is not null then 'user' else 'system' end,
  dr.started_by, coalesce(dr.started_by_email, ''),
  'success', jsonb_build_object('branch', dr.branch, 'kind', dr.kind),
  coalesce(dr.started_at, dr.created_at)
from public.deployment_runs dr
join public.deployments d on d.id = dr.deployment_id
join public.projects p on p.id = d.project_id
union all
select
  'deploy:' || dr.id || ':finished', dr.org_id, d.project_id, p.name,
  'deploy', 'deployment ' || dr.status || ' (' || d.name || ')',
  'deployment', dr.deployment_id, d.name,
  case when dr.started_by is not null then 'user' else 'system' end,
  dr.started_by, coalesce(dr.started_by_email, ''),
  case when dr.status = 'failed' then 'failure' else 'success' end,
  case
    when dr.status = 'failed' then jsonb_build_object(
      'last_event', le.message, 'phase', le.phase,
      'log_tail', right(dr.log, 1500))
    else '{}'::jsonb
  end,
  dr.finished_at
from public.deployment_runs dr
join public.deployments d on d.id = dr.deployment_id
join public.projects p on p.id = d.project_id
left join lateral (
  select e.message, e.phase
  from public.deployment_run_events e
  where e.run_id = dr.id
  order by e.created_at desc
  limit 1
) le on true
where dr.finished_at is not null
union all
select
  'tests:' || tr.id, tr.org_id, tr.project_id, p.name,
  'tests',
  case when tr.source = 'agent'
    then 'test results reported' else 'test run recorded' end,
  case when rr.issue_id is not null then 'issue' else 'run' end,
  coalesce(rr.issue_id, tr.run_id), tr.label,
  case when tr.source = 'agent' then 'worker' else 'user' end,
  tr.started_by, coalesce(tr.worker_name, ''),
  'success', jsonb_build_object('environment', tr.environment),
  coalesce(tr.completed_at, tr.created_at)
from public.test_runs tr
join public.projects p on p.id = tr.project_id
left join public.runs rr on rr.id = tr.run_id
union all
select
  'learning:' || ls.id || ':submitted', ls.org_id, ls.project_id, p.name,
  'learning', 'learning submitted',
  'submission', ls.id, left(ls.text, 120),
  'worker', null::uuid, coalesce(w.name, ''),
  case when ls.status = 'pending' then 'pending' else 'success' end,
  '{}'::jsonb, ls.created_at
from public.learning_submissions ls
join public.projects p on p.id = ls.project_id
left join public.workers w on w.id = ls.worker_id
union all
select
  'learning:' || ls.id || ':decided', ls.org_id, ls.project_id, p.name,
  'learning', 'learning ' || ls.status,
  'submission', ls.id, left(ls.text, 120),
  'user', ls.decided_by, '', 'success',
  jsonb_build_object('note', ls.decision_note), ls.decided_at
from public.learning_submissions ls
join public.projects p on p.id = ls.project_id
where ls.decided_at is not null
union all
select
  'guideline:' || gr.id || ':submitted', gr.org_id, gr.project_id, p.name,
  'guideline', 'guideline change recommended (' || gr.severity || ')',
  'section', gr.section_id, gr.section_title,
  'worker', null::uuid, coalesce(w.name, ''),
  case when gr.status = 'pending' then 'pending' else 'success' end,
  jsonb_build_object('severity', gr.severity, 'rationale', gr.rationale),
  gr.created_at
from public.guideline_recommendations gr
join public.projects p on p.id = gr.project_id
left join public.workers w on w.id = gr.worker_id
union all
select
  'guideline:' || gr.id || ':decided', gr.org_id, gr.project_id, p.name,
  'guideline', 'guideline recommendation ' || gr.status,
  'section', gr.section_id, gr.section_title,
  'user', gr.decided_by, '', 'success',
  jsonb_build_object('note', gr.decision_note, 'severity', gr.severity),
  gr.decided_at
from public.guideline_recommendations gr
join public.projects p on p.id = gr.project_id
where gr.decided_at is not null
union all
select
  'content:' || ca.id, ca.org_id, ca.project_id, p.name,
  'content', ca.surface || ' ' || ca.action || ' — ' || ca.item_key,
  'document', ca.project_id, ca.item_key,
  ca.actor_type, ca.actor_id, ca.actor_name,
  'success', '{}'::jsonb, ca.created_at
from public.content_audit ca
join public.projects p on p.id = ca.project_id
union all
-- Epic lifecycle (US-7.10): opened and completed, derived from epics rows.
select
  'epic:' || e.id || ':opened', e.org_id, e.project_id, p.name,
  'epic', 'epic opened',
  'epic', e.id, 'Epic ' || e.number || ' · ' || e.title,
  case when e.created_by is not null then 'user' else 'system' end,
  e.created_by, '', 'success', '{}'::jsonb, e.created_at
from public.epics e
join public.projects p on p.id = e.project_id
union all
select
  'epic:' || e.id || ':completed', e.org_id, e.project_id, p.name,
  'epic', 'epic completed',
  'epic', e.id, 'Epic ' || e.number || ' · ' || e.title,
  case when e.completed_by is not null then 'user' else 'system' end,
  e.completed_by, '', 'success', '{}'::jsonb, e.completed_at
from public.epics e
join public.projects p on p.id = e.project_id
where e.completed_at is not null;
