-- 230_project_sequential_only: a project-level "one build unit at a time"
-- gate, orthogonal to build_mode (which decides WHO owns a build unit --
-- story, feature, or epic; this decides HOW MANY may be in flight at once).
--
-- Two stories dispatched concurrently in the same project can touch the
-- same files and collide on merge -- GitHub reports a conflict with no
-- automatic recovery. Composes with any build_mode: in `story` mode it
-- serializes stories one at a time through merge; in `feature`/`epic` mode
-- it additionally forbids two different features'/epics' batches from
-- having any story in flight at once.
--
-- Default true: every project (including existing ones -- Postgres
-- backfills the column default onto existing rows) gets strict ordering
-- immediately. A project can opt out via its Task Processing settings.

alter table public.projects
  add column if not exists sequential_only boolean not null default true;

comment on column public.projects.sequential_only is
  'When true, no issue in this project may be dispatched (plan or code) '
  'while another non-abandoned issue in the project is planning, in '
  'plan-review, planned (approved plan, code not yet dispatched), queued, '
  'running, needs-fixes, in-review, or failed. Composes with build_mode.';

-- dispatch_issue: the live body from migration 189, unchanged except for one
-- new guard inserted right after the existing "feature owns the build"
-- refusal and before the previous-run/feedback lookup.
create or replace function public.dispatch_issue(p_issue uuid, p_kind text default null)
returns uuid
language plpgsql
as $$
declare
  v_issue public.issues%rowtype;
  v_project public.projects%rowtype;
  v_prev public.runs%rowtype;
  v_feedback text;
  v_context jsonb;
  v_run uuid;
  v_kind text;
  v_child_count int;
  v_prd_content text;
  v_plan_content text;
  v_test_plan_content text;
  v_pre_status text;
  v_prd_issue uuid;
  v_holder record;
begin
  select * into v_issue from public.issues where id = p_issue for update;
  if not found then
    raise exception 'issue not found';
  end if;
  if v_issue.abandoned_at is not null then
    raise exception 'issue is abandoned';
  end if;

  select count(*) into v_child_count
  from public.issues
  where parent_id = p_issue and abandoned_at is null;
  if v_issue.type = 'feature' and v_child_count > 0 then
    raise exception 'feature with child stories is not dispatchable';
  end if;

  v_kind := public.dispatch_kind_for(p_issue, p_kind);

  select * into v_project from public.projects where id = v_issue.project_id;

  if v_kind = 'code'
     and coalesce(v_project.build_mode, 'story') in ('feature', 'epic')
     and v_issue.parent_id is not null
     and v_issue.status not in ('failed', 'needs-fixes')
  then
    declare
      v_parent_label text;
      v_sibling_count int;
    begin
      select coalesce(
               case when e.number is not null and p.item_no is not null
                 then 'FEAT-' || e.number || '.' || p.item_no
               end,
               p.title)
        into v_parent_label
      from public.issues p
      left join public.epics e on e.id = p.epic_id
      where p.id = v_issue.parent_id;

      select count(*) into v_sibling_count
      from public.issues c
      where c.parent_id = v_issue.parent_id and c.abandoned_at is null;

      raise exception
        '% owns the build — dispatch the feature to build all % stories',
        coalesce(v_parent_label, 'the feature'), v_sibling_count;
    end;
  end if;

  -- US-<sequential>: one build unit in flight per project. A dispatch-time
  -- refusal (not a post-hoc hold on an already-created run) so the manager
  -- cannot even queue a second story while an earlier one is unfinished.
  if coalesce(v_project.sequential_only, false) then
    select i.id, i.title,
           coalesce(
             case when e.number is not null and i.item_no is not null
               then 'US-' || e.number || '.' || i.item_no
             end,
             i.title
           ) as label
      into v_holder
    from public.issues i
    left join public.epics e on e.id = i.epic_id
    where i.project_id = v_issue.project_id
      and i.id <> v_issue.id
      and i.abandoned_at is null
      and i.status in ('planning', 'plan-review', 'planned', 'queued',
                        'running', 'needs-fixes', 'in-review', 'failed')
    order by i.updated_at asc
    limit 1;

    if found then
      raise exception
        '% must reach merged before you can dispatch a new one (sequential build mode)',
        v_holder.label;
    end if;
  end if;

  select * into v_prev
  from public.runs
  where issue_id = p_issue and kind = v_kind
  order by created_at desc
  limit 1;

  v_feedback := null;
  if v_prev.id is not null then
    if v_kind = 'code' then
      select a.comment into v_feedback
      from public.approvals a
      where a.subject_type = 'run'
        and a.subject_id = v_prev.id
        and a.gate = 'code-review'
        and a.decision = 'rejected'
      order by a.created_at desc
      limit 1;
    else
      select a.comment into v_feedback
      from public.approvals a
      where a.issue_id = p_issue
        and a.gate = 'plan'
        and a.decision = 'sent-back'
      order by a.created_at desc
      limit 1;
    end if;
  end if;

  v_prd_issue := coalesce(v_issue.parent_id, case when v_issue.type = 'feature' then v_issue.id end);

  v_context := jsonb_build_object(
    'title', v_issue.title,
    'type', v_issue.type,
    'story', v_issue.body,
    'body', v_issue.body,
    'acceptance_criteria', v_issue.acceptance_criteria,
    'repo_full_name', v_project.repo_full_name,
    'default_branch', v_project.default_branch,
    'run_kind', v_kind,
    'guidelines', public.assemble_project_guidelines(v_issue.project_id),
    'learnings', public.assemble_project_learnings(v_issue.project_id),
    'documents', coalesce((
      select jsonb_agg(jsonb_build_object(
        'id', d.id,
        'name', d.name,
        'mime_type', d.mime_type,
        'size_bytes', d.size_bytes,
        'attached_to', d.attached_to
      ) order by d.created_at)
      from public.documents d
      where (d.issue_id = p_issue and d.attached_to = 'work-item')
         or (v_prd_issue is not null
             and d.issue_id = v_prd_issue and d.attached_to = 'prd')
    ), '[]'::jsonb),
    'test_cases', coalesce((
      select jsonb_agg(jsonb_build_object(
        'id', t.id,
        'title', t.title,
        'steps', t.steps,
        'expected_result', t.expected_result
      ) order by t.created_at)
      from public.test_cases t
      where t.issue_id = p_issue and t.status = 'active'
    ), '[]'::jsonb)
  );

  if v_prd_issue is not null then
    select a.content into v_prd_content
    from public.artifacts a
    where a.issue_id = v_prd_issue and a.kind = 'prd' and a.status = 'approved'
    order by a.version desc limit 1;
    if v_prd_content is not null then
      v_context := v_context || jsonb_build_object('prd', v_prd_content);
    end if;
  end if;

  if v_kind = 'code' then
    select a.content into v_plan_content
    from public.artifacts a
    where a.issue_id = p_issue and a.kind = 'plan' and a.status = 'approved'
    order by a.version desc limit 1;
    select a.content into v_test_plan_content
    from public.artifacts a
    where a.issue_id = p_issue and a.kind = 'test_plan' and a.status = 'approved'
    order by a.version desc limit 1;
    v_context := v_context || jsonb_build_object(
      'plan', v_plan_content,
      'test_plan', v_test_plan_content
    );
  else
    select a.content into v_plan_content
    from public.artifacts a
    where a.issue_id = p_issue and a.kind = 'plan'
    order by a.version desc limit 1;
    if v_plan_content is not null then
      v_context := v_context || jsonb_build_object('previous_plan', v_plan_content);
    end if;
  end if;

  if v_feedback is not null then
    v_context := v_context || jsonb_build_object(
      'feedback', v_feedback,
      'previous_branch', v_prev.branch_ref,
      'previous_pr_url', v_prev.pr_url
    );
  end if;

  perform public.seed_issue_instructions(p_issue, v_kind);

  v_pre_status := v_issue.status;

  insert into public.runs (org_id, issue_id, provider, status, kind, input_context, prev_issue_status)
  values (v_issue.org_id, p_issue, 'claude', 'queued', v_kind, v_context, v_pre_status)
  returning id into v_run;

  update public.issues set status = 'queued' where id = p_issue;

  insert into public.issue_events (org_id, issue_id, type, payload)
  values (
    v_issue.org_id,
    p_issue,
    case when v_kind = 'plan' then 'plan-dispatched' else 'dispatched' end,
    jsonb_build_object(
      'run_id', v_run,
      'kind', v_kind,
      'from_status', v_pre_status,
      'kind_chosen_by', case when p_kind is null then 'inferred' else 'manager' end
    )
  );

  return v_run;
end;
$$;
