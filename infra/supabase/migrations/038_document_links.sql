-- 038_document_links: document link targets + PRD docs in dispatch (US-2.22).
--
-- A document links to exactly one of: the project, a work item, a work
-- item's PRD, or a test case. The PRD link is `attached_to = 'prd'` +
-- the feature's issue_id — it rides the issue's PRD chain across
-- versions (deliberately not an FK to one artifact version row, so
-- re-drafting/superseding never orphans a wireframe).

-- Composite FK target for documents -> test_cases (008 predates the
-- (id, org_id) convention).
do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'test_cases_id_org_unique'
  ) then
    alter table public.test_cases
      add constraint test_cases_id_org_unique unique (id, org_id);
  end if;
end $$;

alter table public.documents
  add column attached_to text not null default 'project'
    check (attached_to in ('project', 'work-item', 'prd', 'test-case')),
  add column test_case_id uuid;

alter table public.documents
  add constraint documents_test_case_org_fk
  foreign key (test_case_id, org_id)
  references public.test_cases (id, org_id) on delete set null (test_case_id);

create index documents_test_case_idx
  on public.documents (test_case_id) where test_case_id is not null;

-- Backfill: rows created under 037 were work-item-attached iff issue_id set.
update public.documents
  set attached_to = 'work-item'
  where issue_id is not null;

-- Detach must flip the link home to 'project': the FKs' ON DELETE SET
-- NULL is an UPDATE on this row, so a BEFORE UPDATE trigger normalizes
-- before the CHECK below sees it. UPDATE only — a contradictory INSERT
-- is a client bug and must error, not be coerced.
create or replace function public.documents_normalize_link()
returns trigger
language plpgsql
as $$
begin
  if new.issue_id is null and new.attached_to in ('work-item', 'prd') then
    new.attached_to := 'project';
  end if;
  if new.test_case_id is null and new.attached_to = 'test-case' then
    new.attached_to := 'project';
  end if;
  return new;
end;
$$;

create trigger documents_normalize_link
  before update on public.documents
  for each row execute function public.documents_normalize_link();

alter table public.documents
  add constraint documents_link_refs check (
    case attached_to
      when 'project' then issue_id is null and test_case_id is null
      when 'work-item' then issue_id is not null and test_case_id is null
      when 'prd' then issue_id is not null and test_case_id is null
      when 'test-case' then test_case_id is not null and issue_id is null
    end
  );

-- --------------------------------------- dispatch_issue: PRD documents
-- 037's dispatch_issue with the documents key extended: entries carry
-- their link kind, and a work item governed by a PRD (a feature, or a
-- story with a parent feature) also receives the PRD-linked documents.
create or replace function public.dispatch_issue(p_issue uuid)
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
  v_has_approved_plan boolean;
  v_child_count int;
  v_approved_prd_id uuid;
  v_prd_content text;
  v_plan_content text;
  v_test_plan_content text;
  v_pre_status text;
  v_prd_issue uuid;
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

  select exists(
    select 1 from public.artifacts
    where issue_id = p_issue and kind = 'plan' and status = 'approved'
  ) into v_has_approved_plan;

  if v_has_approved_plan and v_issue.status in ('planned', 'needs-fixes') then
    v_kind := 'code';
  elsif v_issue.status in ('draft', 'ready', 'failed') then
    v_kind := 'plan';
  elsif v_issue.status = 'needs-fixes' and not v_has_approved_plan then
    v_kind := 'plan';
  else
    raise exception 'issue is not dispatchable from status "%"', v_issue.status;
  end if;

  if v_kind = 'code' and not v_has_approved_plan then
    raise exception 'code run requires an approved plan';
  end if;

  if v_issue.type = 'feature' and v_kind = 'plan' then
    select id into v_approved_prd_id from public.artifacts
    where issue_id = p_issue and kind = 'prd' and status = 'approved'
    order by version desc limit 1;
    if v_approved_prd_id is null then
      raise exception 'feature requires an approved PRD before planning';
    end if;
  end if;

  select * into v_project from public.projects where id = v_issue.project_id;

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
  elsif v_feedback is not null then
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

  v_pre_status := v_issue.status;

  insert into public.runs (org_id, issue_id, provider, status, kind, input_context)
  values (v_issue.org_id, p_issue, 'claude', 'queued', v_kind, v_context)
  returning id into v_run;

  update public.issues set status = 'queued' where id = p_issue;

  insert into public.issue_events (org_id, issue_id, type, payload)
  values (
    v_issue.org_id,
    p_issue,
    case when v_kind = 'plan' then 'plan-dispatched' else 'dispatched' end,
    jsonb_build_object('run_id', v_run, 'kind', v_kind, 'from_status', v_pre_status)
  );

  return v_run;
end;
$$;
