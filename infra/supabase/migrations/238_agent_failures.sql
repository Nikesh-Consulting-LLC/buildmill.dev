-- 238_agent_failures (US-79.8): an agent that fails leaves a full report.
--
-- Three ways an agent fails — a run reporting `failed`, a lease expiring
-- without a submission, a heartbeat going stale — and none of them reached a
-- console: the System issues inbox (184) only receives exceptions the API
-- process raises, and the sweep that notices a dead agent succeeds. What
-- traces existed answer the wrong questions: run_attempts (152) is a ceiling
-- counter with no error text and no agent identity beyond a worker id;
-- issue_events buries the fact inside one work item's feed; and issue-less
-- runs (deploy, release prep, test) have no feed at all.
--
-- One row per failure event, append-only, never deduped: each failure's
-- value is its individual run detail — collapsing two lease expiries under
-- one fingerprint is exactly what app_issues would have done and exactly
-- what a debugger doesn't want.
--
-- Agent identity is SNAPSHOTTED (worker name/type, preset) because a requeue
-- reassigns the run row — the run cannot answer "who failed" afterwards. The
-- instruction bundle is the opposite case: runs.input_context is written at
-- dispatch and never rewritten by claim or requeue, so it is read THROUGH
-- run_id (agent_failure_run_context below) rather than copied into every
-- row. issue_id / project_id / run_id are deliberately plain uuids, not
-- foreign keys: the report must survive its run being cascade-deleted, and a
-- plain column adds no PostgREST relationship for the embed-ambiguity trap
-- (PGRST201) to bite on.

create table public.agent_failures (
  id bigint generated always as identity primary key,
  org_id uuid not null references public.organizations(id) on delete cascade,
  project_id uuid,
  issue_id uuid,
  run_id uuid,
  kind text not null,
  worker_id uuid,
  worker_name text not null default '',
  worker_type text not null default '',
  preset_name text,
  preset_version int,
  category text not null check (category in (
    'run-failed', 'lease-expired', 'heartbeat-stale'
  )),
  error text,
  detail jsonb not null default '{}'::jsonb,
  resumable boolean not null default false,
  status text not null default 'new' check (status in ('new', 'reviewed')),
  created_at timestamptz not null default now()
);

create index agent_failures_created_idx
  on public.agent_failures (created_at desc);

alter table public.agent_failures enable row level security;

-- Superadmin surface, same boundary as /admin/system-issues: platform admins
-- read and triage across every org; org members get NO policy (an org-facing
-- view is a later story — the shape here deliberately leaves room for it).
-- Writes are service-role only: no insert policy exists on purpose.
create policy "platform admins read agent failures"
  on public.agent_failures for select
  using (public.is_platform_admin());

create policy "platform admins triage agent failures"
  on public.agent_failures for update
  using (public.is_platform_admin())
  with check (public.is_platform_admin());

comment on table public.agent_failures is
  'US-79.8: append-only log of agent failures — a run reporting failed, a '
  'lease expiring without a submission, a heartbeat going stale. One row per '
  'event, never deduped. Agent identity is snapshotted at failure time '
  'because requeue reassigns the run row; instructions are read through '
  'run_id, which is a plain uuid (not an FK) so the report survives its run '
  'being cascade-deleted.';

comment on column public.agent_failures.resumable is
  'True only for the stale-heartbeat landing that parked the run for its '
  'worker to resume (US-59.4) rather than requeueing it for anyone.';

-- ----------------------------------------------------- the console's reads
-- organizations / projects / issues / runs are member-scoped and stay that
-- way; the console resolves display names through this security definer
-- function instead of new table policies.

create or replace function public.list_agent_failures(p_limit int default 500)
returns table (
  id bigint,
  created_at timestamptz,
  org_id uuid,
  org_name text,
  project_id uuid,
  project_name text,
  issue_id uuid,
  issue_type text,
  issue_title text,
  run_id uuid,
  run_exists boolean,
  kind text,
  worker_id uuid,
  worker_name text,
  worker_type text,
  preset_name text,
  preset_version int,
  category text,
  error text,
  detail jsonb,
  resumable boolean,
  status text
)
language sql
stable
security definer
set search_path = public
as $$
  select f.id, f.created_at, f.org_id, coalesce(o.name, '') as org_name,
         f.project_id, coalesce(p.name, '') as project_name,
         f.issue_id, i.type as issue_type, i.title as issue_title,
         f.run_id, (r.id is not null) as run_exists,
         f.kind, f.worker_id, f.worker_name, f.worker_type,
         f.preset_name, f.preset_version,
         f.category, f.error, f.detail, f.resumable, f.status
  from public.agent_failures f
  left join public.organizations o on o.id = f.org_id
  left join public.projects p on p.id = f.project_id
  left join public.issues i on i.id = f.issue_id
  left join public.runs r on r.id = f.run_id
  where public.is_platform_admin()
  order by f.created_at desc
  limit greatest(1, least(coalesce(p_limit, 500), 1000));
$$;

revoke execute on function public.list_agent_failures(int) from public, anon;
grant execute on function public.list_agent_failures(int) to authenticated;

comment on function public.list_agent_failures(int) is
  'US-79.8: the Agent failures console''s listing. Security definer so the '
  'display names of member-scoped neighbours (org, project, issue) resolve '
  'without widening any table policy; the is_platform_admin() predicate is '
  'the gate.';

-- The instruction bundle, on expand. Reads THROUGH the run reference so
-- nothing is duplicated; a deleted run returns null and the console says so.
create or replace function public.agent_failure_run_context(p_failure bigint)
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  select r.input_context
  from public.agent_failures f
  join public.runs r on r.id = f.run_id
  where f.id = p_failure and public.is_platform_admin();
$$;

revoke execute on function public.agent_failure_run_context(bigint) from public, anon;
grant execute on function public.agent_failure_run_context(bigint) to authenticated;

comment on function public.agent_failure_run_context(bigint) is
  'US-79.8: the failed run''s input_context (the instruction bundle it was '
  'dispatched with) for the console''s expanded view. Null when the run no '
  'longer exists or the caller is not a platform admin.';
