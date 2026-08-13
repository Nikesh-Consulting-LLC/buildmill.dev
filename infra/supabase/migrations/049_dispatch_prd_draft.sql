-- 049_dispatch_prd_draft: PRD drafting joins the worker pool (US-3.21).
--
-- runs.kind gains 'prd' — a run with no repo/branch fields, fulfilled by a
-- direct LLM call instead of the Claude Code CLI-against-a-checkout flow
-- plan/code runs use. dispatch_prd_draft mirrors dispatch_issue's shape
-- but is deliberately its own function: PRD dispatch has none of
-- dispatch_issue's plan-vs-code kind resolution, child-count guard, or
-- approved-plan requirement.

alter table public.runs drop constraint runs_kind_check;
alter table public.runs add constraint runs_kind_check
  check (kind in ('plan', 'code', 'prd'));

create or replace function public.dispatch_prd_draft(p_issue uuid)
returns uuid
language plpgsql
as $$
declare
  v_issue public.issues%rowtype;
  v_prior public.artifacts%rowtype;
  v_feedback text;
  v_context jsonb;
  v_run uuid;
begin
  select * into v_issue from public.issues where id = p_issue for update;
  if not found then
    raise exception 'issue not found';
  end if;
  if v_issue.abandoned_at is not null then
    raise exception 'issue is abandoned';
  end if;
  if v_issue.type <> 'feature' then
    raise exception 'PRDs are only for feature issues';
  end if;
  if v_issue.status not in ('draft', 'prd-review', 'ready') then
    raise exception 'cannot draft PRD from status "%"', v_issue.status;
  end if;

  select * into v_prior
  from public.artifacts
  where issue_id = p_issue and kind = 'prd'
  order by version desc limit 1;

  if v_prior.id is not null then
    select a.comment into v_feedback
    from public.approvals a
    where a.issue_id = p_issue and a.gate = 'prd' and a.decision = 'sent-back'
    order by a.created_at desc limit 1;
  end if;

  v_context := jsonb_build_object(
    'title', v_issue.title,
    'type', v_issue.type,
    'story', v_issue.body,
    'body', v_issue.body,
    'run_kind', 'prd',
    'guidelines', public.assemble_project_guidelines(v_issue.project_id),
    'learnings', public.assemble_project_learnings(v_issue.project_id)
  );
  if v_prior.id is not null then
    v_context := v_context || jsonb_build_object('previous_prd', v_prior.content);
  end if;
  if v_feedback is not null then
    v_context := v_context || jsonb_build_object('feedback', v_feedback);
  end if;

  insert into public.runs (org_id, issue_id, provider, status, kind, input_context)
  values (v_issue.org_id, p_issue, 'claude', 'queued', 'prd', v_context)
  returning id into v_run;

  insert into public.issue_events (org_id, issue_id, type, payload)
  values (v_issue.org_id, p_issue, 'prd-dispatched', jsonb_build_object('run_id', v_run));

  return v_run;
end;
$$;
