-- 037_documents: project document storage (US-2.21).
--
-- Documents are org data, NOT secrets. Unlike the `data` bucket (019 —
-- deliberately policy-free, service-role only; unchanged here), the
-- `project-docs` bucket carries org-scoped storage.objects policies so
-- org members read/write their own org's folder directly from the
-- browser ("build less API"). Object keys follow
-- `<org_id>/projects/<project_id>/<document_id>/<filename>`; metadata
-- lives in public.documents. `api` (service role) bypasses RLS for
-- factory/agent writes and runner reads.

insert into storage.buckets (id, name, public, file_size_limit)
values ('project-docs', 'project-docs', false, 26214400) -- 25 MB, any mime
on conflict (id) do nothing;

-- Storage paths carry the org id as text; a malformed first segment must
-- deny, not raise on the uuid cast.
create or replace function public.is_org_member_text(org text)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select case
    when org ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
      then public.is_org_member(org::uuid)
    else false
  end;
$$;

create policy "org members read their project docs"
  on storage.objects for select
  using (
    bucket_id = 'project-docs'
    and public.is_org_member_text((storage.foldername(name))[1])
  );

create policy "org members upload project docs"
  on storage.objects for insert
  with check (
    bucket_id = 'project-docs'
    and public.is_org_member_text((storage.foldername(name))[1])
  );

create policy "org members replace project docs"
  on storage.objects for update
  using (
    bucket_id = 'project-docs'
    and public.is_org_member_text((storage.foldername(name))[1])
  );

create policy "org members delete project docs"
  on storage.objects for delete
  using (
    bucket_id = 'project-docs'
    and public.is_org_member_text((storage.foldername(name))[1])
  );

-- ------------------------------------------------------------ documents
-- Cross-org integrity: composite (id, org_id) FKs, per 020/031.
create table public.documents (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  project_id uuid not null,
  issue_id uuid,
  run_id uuid,
  name text not null,
  mime_type text not null default 'application/octet-stream',
  size_bytes bigint not null default 0,
  storage_path text not null unique,
  source text not null default 'user' check (source in ('user', 'factory', 'agent')),
  created_by uuid references auth.users(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (id, org_id),
  foreign key (project_id, org_id)
    references public.projects (id, org_id) on delete cascade,
  -- a work item goes away -> its documents stay in the project folder
  -- (the 031 epic pattern; (col) list keeps org_id NOT NULL intact)
  foreign key (issue_id, org_id)
    references public.issues (id, org_id) on delete set null (issue_id),
  foreign key (run_id, org_id)
    references public.runs (id, org_id) on delete set null (run_id)
);

create index documents_org_idx on public.documents (org_id);
create index documents_project_idx on public.documents (project_id, created_at desc);
create index documents_issue_idx on public.documents (issue_id) where issue_id is not null;

create trigger documents_touch
  before update on public.documents
  for each row execute function public.touch_updated_at();

alter table public.documents enable row level security;

create policy "members manage their org documents"
  on public.documents for all
  using (public.is_org_member(org_id))
  with check (public.is_org_member(org_id));

-- Agent uploads appear in an open Documents panel without a refresh.
alter publication supabase_realtime add table public.documents;

-- ------------------------------------------- dispatch_issue: documents
-- 035's dispatch_issue verbatim plus a `documents` key: metadata for the
-- work item's attached documents at dispatch time. Bytes never enter
-- input_context — the runner fetches them from `api` (US-2.21).
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
    'run_kind', v_kind,
    'guidelines', public.assemble_project_guidelines(v_issue.project_id),
    'learnings', public.assemble_project_learnings(v_issue.project_id),
    'documents', coalesce((
      select jsonb_agg(jsonb_build_object(
        'id', d.id,
        'name', d.name,
        'mime_type', d.mime_type,
        'size_bytes', d.size_bytes
      ) order by d.created_at)
      from public.documents d
      where d.issue_id = p_issue
    ), '[]'::jsonb)
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
