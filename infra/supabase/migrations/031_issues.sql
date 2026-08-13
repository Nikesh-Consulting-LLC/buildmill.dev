-- 031_issues: the Phase-2 core schema (US-2.1). The atomic unit becomes a
-- typed issue; tasks/task_events/reviews are replaced, not migrated —
-- they are empty (verified at authoring time and asserted below).
--
-- Cross-org integrity: composite (id, org_id) FKs, per 020_deployments.

-- Guard: this migration DROPS tables. If a row ever appeared, fail loudly
-- rather than delete it silently.
do $$
declare
  v_n bigint;
begin
  select count(*) into v_n from public.tasks;
  if v_n > 0 then raise exception 'tasks is not empty (% rows) — US-2.1 assumed an empty table; a data migration is required', v_n; end if;
  select count(*) into v_n from public.task_events;
  if v_n > 0 then raise exception 'task_events is not empty (% rows)', v_n; end if;
  select count(*) into v_n from public.reviews;
  if v_n > 0 then raise exception 'reviews is not empty (% rows)', v_n; end if;
  select count(*) into v_n from public.runs;
  if v_n > 0 then raise exception 'runs is not empty (% rows)', v_n; end if;
  select count(*) into v_n from public.test_cases;
  if v_n > 0 then raise exception 'test_cases is not empty (% rows)', v_n; end if;
end $$;

-- ---------------------------------------------------------------- epics
-- Table only — no UI until US-2.8.
create table public.epics (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  project_id uuid not null,
  title text not null,
  description text,
  status text not null default 'open' check (status in ('open', 'completed')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (id, org_id),
  foreign key (project_id, org_id)
    references public.projects (id, org_id) on delete cascade
);

create index epics_org_idx on public.epics (org_id);
create index epics_project_idx on public.epics (project_id);

-- --------------------------------------------------------------- issues
create table public.issues (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  project_id uuid not null,
  type text not null default 'story'
    check (type in ('feature', 'bug', 'chore', 'story')),
  parent_id uuid,
  epic_id uuid,
  title text not null,
  body text,
  acceptance_criteria jsonb not null default '[]'::jsonb,
  status text not null default 'draft'
    check (status in (
      'draft', 'prd-review', 'ready', 'planning', 'plan-review', 'planned',
      'queued', 'running', 'needs-fixes', 'in-review', 'merged', 'failed', 'done'
    )),
  github_issue_number integer,
  github_issue_url text,
  abandoned_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (id, org_id),
  foreign key (project_id, org_id)
    references public.projects (id, org_id) on delete cascade,
  -- a parent goes away -> its children become standalone, never deleted.
  -- The (col) list on SET NULL is required: a composite FK would otherwise
  -- null org_id too, which is NOT NULL. Postgres 15+; this project is 17.6.
  foreign key (parent_id, org_id)
    references public.issues (id, org_id) on delete set null (parent_id),
  -- deleting an epic un-assigns its members (US-2.8), never deletes them
  foreign key (epic_id, org_id)
    references public.epics (id, org_id) on delete set null (epic_id)
);

create index issues_org_idx on public.issues (org_id);
create index issues_project_idx on public.issues (project_id);
create index issues_parent_idx on public.issues (parent_id) where parent_id is not null;
create index issues_epic_idx on public.issues (epic_id) where epic_id is not null;
create index issues_active_idx on public.issues (project_id) where abandoned_at is null;

-- Carried from 013: one import per (project, issue number).
create unique index issues_github_issue_unique
  on public.issues (project_id, github_issue_number)
  where github_issue_number is not null;

-- Only a story may have a parent, and that parent must be a feature.
-- A CHECK cannot read another row, so this is a trigger.
create or replace function public.enforce_issue_parent()
returns trigger
language plpgsql
as $$
declare
  v_parent_type text;
begin
  if new.parent_id is null then
    return new;
  end if;
  if new.type <> 'story' then
    raise exception 'only a story may have a parent (this issue is a "%")', new.type;
  end if;
  if new.parent_id = new.id then
    raise exception 'an issue cannot be its own parent';
  end if;
  select type into v_parent_type from public.issues where id = new.parent_id;
  if v_parent_type is null then
    raise exception 'parent issue not found';
  end if;
  if v_parent_type <> 'feature' then
    raise exception 'a story''s parent must be a feature (parent is a "%")', v_parent_type;
  end if;
  return new;
end;
$$;

create trigger issues_enforce_parent
  before insert or update of parent_id, type on public.issues
  for each row execute function public.enforce_issue_parent();

-- Carried from 015: a queued/running issue can't be deleted or abandoned.
create or replace function public.guard_issue_removal()
returns trigger
language plpgsql
as $$
begin
  if TG_OP = 'DELETE' then
    if old.status in ('queued', 'running') then
      raise exception 'Cannot delete an issue that is queued or running.';
    end if;
    return old;
  end if;

  if new.abandoned_at is not null
     and old.abandoned_at is null
     and new.status in ('queued', 'running') then
    raise exception 'Cannot abandon an issue that is queued or running.';
  end if;
  return new;
end;
$$;

create trigger issues_guard_delete
  before delete on public.issues
  for each row execute function public.guard_issue_removal();

create trigger issues_guard_abandon
  before update of abandoned_at on public.issues
  for each row execute function public.guard_issue_removal();

create trigger issues_touch
  before update on public.issues
  for each row execute function public.touch_updated_at();

-- --------------------------------------------------------- issue_events
create table public.issue_events (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  issue_id uuid not null,
  type text not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  foreign key (issue_id, org_id)
    references public.issues (id, org_id) on delete cascade
);

create index issue_events_issue_idx on public.issue_events (issue_id, created_at);

-- ------------------------------------------------------------ artifacts
create table public.artifacts (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  issue_id uuid not null,
  kind text not null check (kind in ('prd', 'plan', 'test_plan')),
  content text not null default '',
  version integer not null default 1,
  status text not null default 'draft'
    check (status in ('draft', 'approved', 'superseded')),
  created_by text not null check (created_by in ('human', 'llm', 'agent')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (id, org_id),
  unique (issue_id, kind, version),
  foreign key (issue_id, org_id)
    references public.issues (id, org_id) on delete cascade
);

create index artifacts_issue_idx on public.artifacts (issue_id, kind, version);
create index artifacts_org_idx on public.artifacts (org_id);

create trigger artifacts_touch
  before update on public.artifacts
  for each row execute function public.touch_updated_at();

-- ------------------------------------------------------------ approvals
-- Every decision point in the factory, one row each. Append-only.
-- subject_type/subject_id are a loose ref (artifact | run | release_record)
-- deliberately not FK'd: the log outlives the thing it decided on.
create table public.approvals (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  issue_id uuid not null,
  gate text not null check (gate in (
    'prd', 'plan', 'code-review', 'qa-signoff', 'merge-override', 'promotion'
  )),
  subject_type text check (subject_type in ('artifact', 'run', 'release_record')),
  subject_id uuid,
  decision text not null check (decision in ('approved', 'rejected', 'sent-back')),
  comment text,
  actor uuid not null references auth.users(id),
  created_at timestamptz not null default now(),
  foreign key (issue_id, org_id)
    references public.issues (id, org_id) on delete cascade
);

create index approvals_issue_idx on public.approvals (issue_id, created_at);
create index approvals_org_idx on public.approvals (org_id, created_at);
create index approvals_gate_idx on public.approvals (org_id, gate);

-- ------------------------------------------------------------------ RLS
alter table public.epics enable row level security;
alter table public.issues enable row level security;
alter table public.issue_events enable row level security;
alter table public.artifacts enable row level security;
alter table public.approvals enable row level security;

create policy "members manage their org epics"
  on public.epics for all
  using (public.is_org_member(org_id))
  with check (public.is_org_member(org_id));

create policy "members manage their org issues"
  on public.issues for all
  using (public.is_org_member(org_id))
  with check (public.is_org_member(org_id));

create policy "members manage their org artifacts"
  on public.artifacts for all
  using (public.is_org_member(org_id))
  with check (public.is_org_member(org_id));

-- Append-only: select + insert only, no update/delete policies.
create policy "members read their org issue events"
  on public.issue_events for select
  using (public.is_org_member(org_id));

create policy "members append issue events"
  on public.issue_events for insert
  with check (public.is_org_member(org_id));

create policy "members read their org approvals"
  on public.approvals for select
  using (public.is_org_member(org_id));

create policy "members append approvals"
  on public.approvals for insert
  with check (public.is_org_member(org_id));

-- ------------------------------------------------------------- realtime
-- The board (US-1.7) subscribes to issue changes.
alter table public.issues replica identity full;
alter publication supabase_realtime add table public.issues;
alter publication supabase_realtime drop table public.tasks;

-- --------------------------------------------- re-point the survivors
alter table public.runs
  add column issue_id uuid,
  add column kind text not null default 'code' check (kind in ('plan', 'code'));

-- Empty table (asserted above), so the FK and not-null are free.
alter table public.runs drop column task_id;
alter table public.runs alter column issue_id set not null;
alter table public.runs
  add constraint runs_issue_org_fk
  foreign key (issue_id, org_id)
  references public.issues (id, org_id) on delete cascade;

drop index if exists public.runs_task_idx;
create index runs_issue_idx on public.runs (issue_id, created_at);
create index runs_kind_idx on public.runs (issue_id, kind, created_at);

alter table public.test_cases add column issue_id uuid;
alter table public.test_cases drop column task_id;
-- set null (issue_id) only — a bare SET NULL would null org_id, which is NOT NULL.
alter table public.test_cases
  add constraint test_cases_issue_org_fk
  foreign key (issue_id, org_id)
  references public.issues (id, org_id) on delete set null (issue_id);

create index test_cases_issue_idx on public.test_cases (issue_id) where issue_id is not null;

-- ------------------------------------------------- drop the dead tables
-- reviews first: approve_run/reject_run are recreated below without it.
drop table public.reviews;
drop table public.task_events;
drop trigger if exists tasks_guard_delete on public.tasks;
drop trigger if exists tasks_guard_abandon on public.tasks;
drop table public.tasks;
drop function if exists public.dispatch_task(uuid);
drop function if exists public.guard_task_removal();

-- ------------------------------------------------ functions, rebuilt
-- Same semantics as 006, against issues, writing approvals not reviews.
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
begin
  select * into v_issue from public.issues where id = p_issue for update;
  if not found then
    raise exception 'issue not found';
  end if;
  if v_issue.status not in ('draft', 'needs-fixes') then
    raise exception 'issue is not dispatchable from status "%"', v_issue.status;
  end if;

  select * into v_project from public.projects where id = v_issue.project_id;

  select * into v_prev
  from public.runs
  where issue_id = p_issue
  order by created_at desc
  limit 1;

  if v_prev.id is not null then
    select a.comment into v_feedback
    from public.approvals a
    where a.subject_type = 'run'
      and a.subject_id = v_prev.id
      and a.gate = 'code-review'
      and a.decision = 'rejected'
    order by a.created_at desc
    limit 1;
  end if;

  v_context := jsonb_build_object(
    'title', v_issue.title,
    'story', v_issue.body,
    'acceptance_criteria', v_issue.acceptance_criteria,
    'repo_full_name', v_project.repo_full_name,
    'default_branch', v_project.default_branch
  );

  if v_feedback is not null then
    v_context := v_context || jsonb_build_object(
      'feedback', v_feedback,
      'previous_branch', v_prev.branch_ref,
      'previous_pr_url', v_prev.pr_url
    );
  end if;

  insert into public.runs (org_id, issue_id, provider, status, kind, input_context)
  values (v_issue.org_id, p_issue, 'claude', 'queued', 'code', v_context)
  returning id into v_run;

  update public.issues set status = 'queued' where id = p_issue;

  insert into public.issue_events (org_id, issue_id, type, payload)
  values (v_issue.org_id, p_issue, 'dispatched', jsonb_build_object('run_id', v_run));

  return v_run;
end;
$$;

create or replace function public.approve_run(p_run uuid)
returns void
language plpgsql
as $$
declare
  v_run public.runs%rowtype;
  v_issue public.issues%rowtype;
begin
  select * into v_run from public.runs where id = p_run for update;
  if not found then
    raise exception 'run not found';
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

  insert into public.issue_events (org_id, issue_id, type, payload)
  values
    (v_run.org_id, v_issue.id, 'approved', jsonb_build_object('run_id', p_run)),
    (v_run.org_id, v_issue.id, 'merged',
     jsonb_build_object('run_id', p_run, 'pr_url', v_run.pr_url));
end;
$$;

create or replace function public.reject_run(p_run uuid, p_comment text)
returns void
language plpgsql
as $$
declare
  v_run public.runs%rowtype;
  v_issue public.issues%rowtype;
begin
  if p_comment is null or length(trim(p_comment)) = 0 then
    raise exception 'a comment is required to reject';
  end if;

  select * into v_run from public.runs where id = p_run for update;
  if not found then
    raise exception 'run not found';
  end if;
  select * into v_issue from public.issues where id = v_run.issue_id for update;
  if v_issue.status <> 'in-review' then
    raise exception 'issue is not in review (status "%")', v_issue.status;
  end if;

  insert into public.approvals
    (org_id, issue_id, gate, subject_type, subject_id, decision, comment, actor)
  values
    (v_run.org_id, v_issue.id, 'code-review', 'run', p_run, 'rejected',
     p_comment, auth.uid());

  update public.issues set status = 'needs-fixes' where id = v_issue.id;

  insert into public.issue_events (org_id, issue_id, type, payload)
  values (v_run.org_id, v_issue.id, 'rejected',
          jsonb_build_object('run_id', p_run, 'comment', p_comment));
end;
$$;

revoke execute on function public.dispatch_issue(uuid) from public, anon;
grant execute on function public.dispatch_issue(uuid) to authenticated;
revoke execute on function public.approve_run(uuid) from public, anon;
grant execute on function public.approve_run(uuid) to authenticated;
revoke execute on function public.reject_run(uuid, text) from public, anon;
grant execute on function public.reject_run(uuid, text) to authenticated;
