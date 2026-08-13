-- 009_project_guidelines: project guidelines as ordered markdown sections (US-1.18).
-- One shared assembly function (assemble_project_guidelines) produces the
-- project markdown; both dispatch_task and the FastAPI guidelines.md
-- endpoint call it, so there is exactly one implementation.

create table public.project_guidelines (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  section_key text not null default 'custom',
  title text not null,
  content text not null default '',
  sort_order integer not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index project_guidelines_project_idx
  on public.project_guidelines (project_id, sort_order);

-- At most one instance of each catalog section per project; custom
-- sections (section_key = 'custom') are unlimited.
create unique index project_guidelines_unique_catalog_section
  on public.project_guidelines (project_id, section_key)
  where section_key <> 'custom';

alter table public.project_guidelines enable row level security;

create policy "members manage their org project guidelines"
  on public.project_guidelines for all
  using (public.is_org_member(org_id))
  with check (public.is_org_member(org_id));

create trigger project_guidelines_updated_at
  before update on public.project_guidelines
  for each row execute function public.touch_updated_at();

-- The one shared assembly function: "## <title>" + content per non-empty
-- section, in sort_order. Empty/deleted sections are omitted.
create or replace function public.assemble_project_guidelines(p_project uuid)
returns text
language sql
stable
as $$
  select coalesce(
    string_agg(
      '## ' || title || E'\n\n' || content,
      E'\n\n' order by sort_order, created_at
    ),
    ''
  )
  from public.project_guidelines
  where project_id = p_project
    and length(trim(content)) > 0;
$$;

-- dispatch_task v4: bundles the project's assembled guidelines into
-- input_context so every run gets them without re-explaining. Everything
-- else is unchanged from v3 (007_redispatch_failed.sql).
create or replace function public.dispatch_task(p_task uuid)
returns uuid
language plpgsql
as $$
declare
  v_task public.tasks%rowtype;
  v_project public.projects%rowtype;
  v_prev public.runs%rowtype;
  v_feedback text;
  v_guidelines text;
  v_context jsonb;
  v_run uuid;
begin
  select * into v_task from public.tasks where id = p_task for update;
  if not found then
    raise exception 'task not found';
  end if;
  if v_task.status not in ('draft', 'needs-fixes', 'failed') then
    raise exception 'task is not dispatchable from status "%"', v_task.status;
  end if;

  select * into v_project from public.projects where id = v_task.project_id;

  select * into v_prev
  from public.runs
  where task_id = p_task
  order by created_at desc
  limit 1;

  if v_prev.id is not null then
    select r.comment into v_feedback
    from public.reviews r
    where r.run_id = v_prev.id and r.decision = 'rejected'
    order by r.created_at desc
    limit 1;
  end if;

  v_guidelines := public.assemble_project_guidelines(v_task.project_id);

  v_context := jsonb_build_object(
    'title', v_task.title,
    'story', v_task.story,
    'acceptance_criteria', v_task.acceptance_criteria,
    'repo_full_name', v_project.repo_full_name,
    'default_branch', v_project.default_branch,
    'guidelines', v_guidelines
  );

  if v_feedback is not null then
    v_context := v_context || jsonb_build_object(
      'feedback', v_feedback,
      'previous_branch', v_prev.branch_ref,
      'previous_pr_url', v_prev.pr_url
    );
  end if;

  insert into public.runs (org_id, task_id, provider, status, input_context)
  values (v_task.org_id, p_task, 'claude', 'queued', v_context)
  returning id into v_run;

  update public.tasks set status = 'queued' where id = p_task;

  insert into public.task_events (org_id, task_id, type, payload)
  values (v_task.org_id, p_task, 'dispatched', jsonb_build_object('run_id', v_run));

  return v_run;
end;
$$;
