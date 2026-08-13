-- 113_project_scoped_runs: US-13.12 — release runs, and the schema
-- foundation they carry: a run can be project-scoped (issue_id null)
-- because a release cut spans many issues. runs.project_id becomes a
-- first-class, NOT NULL, composite-FK'd column (backfilled from the
-- issue; a BEFORE INSERT trigger derives it so the existing dispatch
-- RPCs stay untouched). The activity feed gains branches for issue-less
-- runs, named by project. us-13.13's deploy runs ride this same shape.

alter table public.runs add column if not exists project_id uuid;

update public.runs r
set project_id = i.project_id
from public.issues i
where i.id = r.issue_id and r.project_id is null;

alter table public.runs alter column project_id set not null;
alter table public.runs add constraint runs_project_fk
  foreign key (project_id, org_id)
  references public.projects (id, org_id) on delete cascade;
create index runs_project_idx on public.runs (project_id, created_at desc);

-- The dispatch RPCs (dispatch_issue / dispatch_prd_draft /
-- dispatch_breakdown) insert without project_id — derive it from the
-- issue before the NOT NULL check fires.
create or replace function public.runs_fill_project_id()
returns trigger
language plpgsql
as $$
begin
  if new.project_id is null and new.issue_id is not null then
    select project_id into new.project_id
    from public.issues where id = new.issue_id;
  end if;
  return new;
end;
$$;

drop trigger if exists runs_fill_project_id on public.runs;
create trigger runs_fill_project_id
  before insert on public.runs
  for each row execute function public.runs_fill_project_id();

alter table public.runs alter column issue_id drop not null;
alter table public.runs add constraint runs_issue_or_project_scoped
  check (issue_id is not null or kind in ('release'));

alter table public.runs drop constraint runs_kind_check;
alter table public.runs add constraint runs_kind_check
  check (kind in ('plan', 'code', 'prd', 'breakdown', 'test', 'release'));

-- Activity feed: issue-less runs appear named by their project. The full
-- view is re-declared (081's body, with LEFT-JOIN-tolerant additions at
-- the end for project-scoped runs).
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
-- US-13.12: project-scoped (issue-less) runs, named by the project.
select
  'run:' || r.id || ':queued', r.org_id, r.project_id, p.name,
  'run', r.kind || ' run dispatched',
  'project', r.project_id, p.name,
  'system', null::uuid, 'factory', 'success', '{}'::jsonb, r.created_at
from public.runs r
join public.projects p on p.id = r.project_id
where r.issue_id is null
union all
select
  'run:' || r.id || ':finished', r.org_id, r.project_id, p.name,
  'run',
  case
    when r.status = 'succeeded' then r.kind || ' run submitted'
    else r.kind || ' run failed'
  end,
  'project', r.project_id, p.name,
  'worker', null::uuid, coalesce(w.name, ''),
  case when r.status = 'failed' then 'failure' else 'success' end,
  case
    when r.status = 'failed' then jsonb_build_object('error', r.error)
    else jsonb_build_object('pr_url', r.pr_url)
  end,
  r.finished_at
from public.runs r
join public.projects p on p.id = r.project_id
left join public.workers w on w.id = r.worker_id
where r.issue_id is null and r.finished_at is not null
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
where e.completed_at is not null
union all
select
  'release-event:' || rre.id, rre.org_id, i.project_id, p.name,
  'release',
  case rre.kind
    when 'qa-signoff' then 'QA sign-off — ' || upper(rre.environment)
    else 'production promotion approved'
  end,
  'issue', rr.issue_id, i.title,
  case when rre.actor is not null then 'user' else 'system' end,
  rre.actor, '', 'success',
  jsonb_build_object('environment', rre.environment, 'comment', rre.comment),
  rre.created_at
from public.release_record_events rre
join public.release_records rr on rr.id = rre.release_record_id
join public.issues i on i.id = rr.issue_id
join public.projects p on p.id = i.project_id
where rre.kind in ('qa-signoff', 'promotion-approved');
