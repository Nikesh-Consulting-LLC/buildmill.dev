-- 033_phase2_workflow: two-phase dispatch, release records, deployment env
-- (US-2.5, US-2.6 materialize hook, US-2.9).

-- ------------------------------------------------ deployments.environment
alter table public.deployments
  add column if not exists environment text
  check (environment is null or environment in ('dev', 'uat', 'production'));

-- Composite FK target for release_records → runs
do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'runs_id_org_unique'
  ) then
    alter table public.runs add constraint runs_id_org_unique unique (id, org_id);
  end if;
end $$;

-- ------------------------------------------------------ release_records
create table public.release_records (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  issue_id uuid not null,
  run_id uuid not null,
  merge_commit_sha text,
  created_at timestamptz not null default now(),
  unique (id, org_id),
  unique (issue_id),
  foreign key (issue_id, org_id)
    references public.issues (id, org_id) on delete cascade,
  foreign key (run_id, org_id)
    references public.runs (id, org_id) on delete cascade
);

create index release_records_org_idx on public.release_records (org_id, created_at desc);
create index release_records_issue_idx on public.release_records (issue_id);

create table public.release_record_events (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  release_record_id uuid not null,
  environment text not null check (environment in ('dev', 'uat', 'production')),
  kind text not null check (kind in ('deployed', 'qa-signoff', 'promotion-approved')),
  deployment_run_id uuid,
  actor uuid references auth.users(id),
  comment text,
  created_at timestamptz not null default now(),
  foreign key (release_record_id, org_id)
    references public.release_records (id, org_id) on delete cascade
);

create index release_record_events_record_idx
  on public.release_record_events (release_record_id, created_at);

alter table public.release_records enable row level security;
alter table public.release_record_events enable row level security;

create policy "members manage their org release records"
  on public.release_records for all
  using (public.is_org_member(org_id))
  with check (public.is_org_member(org_id));

create policy "members read their org release record events"
  on public.release_record_events for select
  using (public.is_org_member(org_id));

create policy "members append release record events"
  on public.release_record_events for insert
  with check (public.is_org_member(org_id));

-- -------------------------------------------- two-phase dispatch_issue
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

  v_context := jsonb_build_object(
    'title', v_issue.title,
    'type', v_issue.type,
    'story', v_issue.body,
    'body', v_issue.body,
    'acceptance_criteria', v_issue.acceptance_criteria,
    'repo_full_name', v_project.repo_full_name,
    'default_branch', v_project.default_branch,
    'run_kind', v_kind
  );

  v_prd_issue := coalesce(v_issue.parent_id, case when v_issue.type = 'feature' then v_issue.id end);
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

-- ------------------------------------------------ approve_run + release
create or replace function public.approve_run(p_run uuid)
returns void
language plpgsql
as $$
declare
  v_run public.runs%rowtype;
  v_issue public.issues%rowtype;
  v_record_id uuid;
begin
  select * into v_run from public.runs where id = p_run for update;
  if not found then
    raise exception 'run not found';
  end if;
  if v_run.kind <> 'code' then
    raise exception 'approve_run only applies to code runs';
  end if;
  select * into v_issue from public.issues where id = v_run.issue_id for update;
  if v_issue.status <> 'in-review' then
    raise exception 'issue is not in review (status "%")', v_issue.status;
  end if;

  insert into public.approvals
    (org_id, issue_id, gate, subject_type, subject_id, decision, actor)
  values
    (v_run.org_id, v_issue.id, 'code-review', 'run', p_run, 'approved', auth.uid());

  update public.issues set status = 'merged' where id = v_issue.id;

  insert into public.release_records (org_id, issue_id, run_id, merge_commit_sha)
  values (v_run.org_id, v_issue.id, p_run, v_run.merge_commit_sha)
  on conflict (issue_id) do update
    set run_id = excluded.run_id,
        merge_commit_sha = coalesce(excluded.merge_commit_sha, release_records.merge_commit_sha)
  returning id into v_record_id;

  insert into public.issue_events (org_id, issue_id, type, payload)
  values
    (v_run.org_id, v_issue.id, 'approved', jsonb_build_object('run_id', p_run)),
    (v_run.org_id, v_issue.id, 'merged',
     jsonb_build_object('run_id', p_run, 'pr_url', v_run.pr_url, 'release_record_id', v_record_id));

  -- Auto-complete parent feature when all children are merged/done.
  if v_issue.parent_id is not null then
    if not exists (
      select 1 from public.issues c
      where c.parent_id = v_issue.parent_id
        and c.abandoned_at is null
        and c.status not in ('merged', 'done')
    ) then
      update public.issues set status = 'done' where id = v_issue.parent_id;
      insert into public.issue_events (org_id, issue_id, type, payload)
      values (
        v_run.org_id, v_issue.parent_id, 'feature-completed',
        jsonb_build_object('trigger_issue_id', v_issue.id)
      );
    end if;
  end if;
end;
$$;

revoke execute on function public.dispatch_issue(uuid) from public, anon;
grant execute on function public.dispatch_issue(uuid) to authenticated;
revoke execute on function public.approve_run(uuid) from public, anon;
grant execute on function public.approve_run(uuid) to authenticated;
