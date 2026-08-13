-- 070_activity_feed: one org-wide answer to "who did what, and did any
-- of it fail?" (US-5.34). A read model over the event sources that
-- already exist — approvals, issue_events, runs, deployment_runs (+ the
-- last event of a failed run), test_runs, learning_submissions,
-- guideline_recommendations, content_audit. Nothing is double-written:
-- steps whose facts live in state tables (dispatch, submit, PR open,
-- deploy start/finish) contribute derived rows, so the feed has no
-- blind spots without new writes.
--
-- SECURITY INVOKER: each member sees exactly what the underlying
-- tables' RLS grants them. Lease minutiae stay out (progress-note
-- heartbeats are excluded); run failures carry the error and a stdout
-- tail; deploy failures carry the last event and a log tail.

create view public.activity_feed
with (security_invoker = true)
as
-- Gate decisions (us-2.7 approvals).
select
  'approval:' || a.id as id,
  a.org_id,
  i.project_id,
  p.name as project_name,
  'gate' as kind,
  a.gate || ' ' || a.decision as action,
  'issue' as object_type,
  a.issue_id as object_id,
  i.title as object_label,
  'user' as actor_type,
  a.actor as actor_id,
  '' as actor_name,
  'success' as outcome,
  jsonb_build_object('comment', a.comment) as detail,
  a.created_at
from public.approvals a
join public.issues i on i.id = a.issue_id
join public.projects p on p.id = i.project_id

union all
-- Issue lifecycle events (claims, hand-backs, merges, clarifications…).
-- progress-note is a lease heartbeat, not activity; run-failed is
-- carried by the richer runs-derived row below.
select
  'event:' || e.id,
  e.org_id,
  i.project_id,
  p.name,
  'issue',
  replace(e.type, '-', ' '),
  'issue',
  e.issue_id,
  i.title,
  case
    when e.payload ? 'worker' or e.payload ->> 'author_kind' = 'worker'
      then 'worker'
    when e.payload ? 'author' then 'user'
    else 'system'
  end,
  null::uuid,
  coalesce(e.payload ->> 'worker', e.payload ->> 'author', ''),
  case when e.type in ('claim-expired') then 'failure' else 'success' end,
  e.payload,
  e.created_at
from public.issue_events e
join public.issues i on i.id = e.issue_id
join public.projects p on p.id = i.project_id
where e.type not in ('progress-note', 'run-failed')

union all
-- Run dispatched (the runs row itself is the dispatch fact).
select
  'run:' || r.id || ':queued',
  r.org_id,
  i.project_id,
  p.name,
  'run',
  r.kind || ' run dispatched',
  'issue',
  i.id,
  i.title,
  'system',
  null::uuid,
  'factory',
  'success',
  '{}'::jsonb,
  r.created_at
from public.runs r
join public.issues i on i.id = r.issue_id
join public.projects p on p.id = i.project_id

union all
-- Run finished: submitted (PR opened) or failed with the error + stdout
-- tail — success needs a glance, failure needs the story.
select
  'run:' || r.id || ':finished',
  r.org_id,
  i.project_id,
  p.name,
  'run',
  case
    when r.status = 'succeeded' and r.pr_url is not null
      then r.kind || ' run submitted — PR opened'
    when r.status = 'succeeded' then r.kind || ' run submitted'
    else r.kind || ' run failed'
  end,
  'issue',
  i.id,
  i.title,
  'worker',
  null::uuid,
  coalesce(w.name, ''),
  case when r.status = 'failed' then 'failure' else 'success' end,
  case
    when r.status = 'failed' then jsonb_build_object(
      'error', r.error,
      'stdout_tail', right(r.stdout, 1500)
    )
    else jsonb_build_object('pr_url', r.pr_url)
  end,
  r.finished_at
from public.runs r
join public.issues i on i.id = r.issue_id
join public.projects p on p.id = i.project_id
left join public.workers w on w.id = r.worker_id
where r.finished_at is not null

union all
-- Deployment started.
select
  'deploy:' || dr.id || ':started',
  dr.org_id,
  d.project_id,
  p.name,
  'deploy',
  'deployment started (' || d.name || ')',
  'deployment',
  dr.deployment_id,
  d.name,
  case when dr.started_by is not null then 'user' else 'system' end,
  dr.started_by,
  coalesce(dr.started_by_email, ''),
  'success',
  jsonb_build_object('branch', dr.branch, 'kind', dr.kind),
  coalesce(dr.started_at, dr.created_at)
from public.deployment_runs dr
join public.deployments d on d.id = dr.deployment_id
join public.projects p on p.id = d.project_id

union all
-- Deployment finished: the failing run carries its last event and a
-- log tail.
select
  'deploy:' || dr.id || ':finished',
  dr.org_id,
  d.project_id,
  p.name,
  'deploy',
  'deployment ' || dr.status || ' (' || d.name || ')',
  'deployment',
  dr.deployment_id,
  d.name,
  case when dr.started_by is not null then 'user' else 'system' end,
  dr.started_by,
  coalesce(dr.started_by_email, ''),
  case when dr.status = 'failed' then 'failure' else 'success' end,
  case
    when dr.status = 'failed' then jsonb_build_object(
      'last_event', le.message,
      'phase', le.phase,
      'log_tail', right(dr.log, 1500)
    )
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
-- Test results reported (us-5.19 agent reports and manual runs alike).
select
  'tests:' || tr.id,
  tr.org_id,
  tr.project_id,
  p.name,
  'tests',
  case when tr.source = 'agent'
    then 'test results reported'
    else 'test run recorded'
  end,
  case when rr.issue_id is not null then 'issue' else 'run' end,
  coalesce(rr.issue_id, tr.run_id),
  tr.label,
  case when tr.source = 'agent' then 'worker' else 'user' end,
  tr.started_by,
  coalesce(tr.worker_name, ''),
  'success',
  jsonb_build_object('environment', tr.environment),
  coalesce(tr.completed_at, tr.created_at)
from public.test_runs tr
join public.projects p on p.id = tr.project_id
left join public.runs rr on rr.id = tr.run_id

union all
-- Learnings: submitted (pending until the manager decides)…
select
  'learning:' || ls.id || ':submitted',
  ls.org_id,
  ls.project_id,
  p.name,
  'learning',
  'learning submitted',
  'submission',
  ls.id,
  left(ls.text, 120),
  'worker',
  null::uuid,
  coalesce(w.name, ''),
  case when ls.status = 'pending' then 'pending' else 'success' end,
  '{}'::jsonb,
  ls.created_at
from public.learning_submissions ls
join public.projects p on p.id = ls.project_id
left join public.workers w on w.id = ls.worker_id

union all
-- …and decided.
select
  'learning:' || ls.id || ':decided',
  ls.org_id,
  ls.project_id,
  p.name,
  'learning',
  'learning ' || ls.status,
  'submission',
  ls.id,
  left(ls.text, 120),
  'user',
  ls.decided_by,
  '',
  'success',
  jsonb_build_object('note', ls.decision_note),
  ls.decided_at
from public.learning_submissions ls
join public.projects p on p.id = ls.project_id
where ls.decided_at is not null

union all
-- Guideline recommendations: submitted…
select
  'guideline:' || gr.id || ':submitted',
  gr.org_id,
  gr.project_id,
  p.name,
  'guideline',
  'guideline change recommended (' || gr.severity || ')',
  'section',
  gr.section_id,
  gr.section_title,
  'worker',
  null::uuid,
  coalesce(w.name, ''),
  case when gr.status = 'pending' then 'pending' else 'success' end,
  jsonb_build_object('severity', gr.severity, 'rationale', gr.rationale),
  gr.created_at
from public.guideline_recommendations gr
join public.projects p on p.id = gr.project_id
left join public.workers w on w.id = gr.worker_id

union all
-- …and decided.
select
  'guideline:' || gr.id || ':decided',
  gr.org_id,
  gr.project_id,
  p.name,
  'guideline',
  'guideline recommendation ' || gr.status,
  'section',
  gr.section_id,
  gr.section_title,
  'user',
  gr.decided_by,
  '',
  'success',
  jsonb_build_object('note', gr.decision_note, 'severity', gr.severity),
  gr.decided_at
from public.guideline_recommendations gr
join public.projects p on p.id = gr.project_id
where gr.decided_at is not null

union all
-- Content changes (us-5.33): the steering surfaces' audit trail.
select
  'content:' || ca.id,
  ca.org_id,
  ca.project_id,
  p.name,
  'content',
  ca.surface || ' ' || ca.action || ' — ' || ca.item_key,
  'document',
  ca.project_id,
  ca.item_key,
  ca.actor_type,
  ca.actor_id,
  ca.actor_name,
  'success',
  '{}'::jsonb,
  ca.created_at
from public.content_audit ca
join public.projects p on p.id = ca.project_id;
