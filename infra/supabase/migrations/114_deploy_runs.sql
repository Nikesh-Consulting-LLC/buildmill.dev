-- 114_deploy_runs: US-13.13 — agent-executed deployments with rails.
-- The `deploy` run kind rides us-13.12's project-scoped runs (issue_id
-- null) and adds runs.deployment_id so the audit trail names the exact
-- deployment definition. The rails: protected deployments are human-only
-- always; production needs the new per-deployment "agent may deploy"
-- flag (default off, flips audited); rollback is pre-authorized at
-- dispatch or it does not happen; the `deploy` capability gates the pool
-- via the 13.10 generic predicate.
--
-- log_deployment_config_change v-next is built from the CURRENT live
-- definition (027) plus the agent-dispatch clause; baked instruction
-- v-next from 112 with all kinds verbatim (the 095/105/106 lesson).

alter table public.runs drop constraint runs_kind_check;
alter table public.runs add constraint runs_kind_check
  check (kind in ('plan', 'code', 'prd', 'breakdown', 'test', 'release',
                  'deploy'));

alter table public.runs drop constraint runs_issue_or_project_scoped;
alter table public.runs add constraint runs_issue_or_project_scoped
  check (issue_id is not null or kind in ('release', 'deploy'));

alter table public.runs add column if not exists deployment_id uuid;
alter table public.runs add constraint runs_deployment_fk
  foreign key (deployment_id, org_id)
  references public.deployments (id, org_id) on delete set null;

alter table public.deployments
  add column if not exists agent_dispatch_allowed boolean not null default false;

comment on column public.deployments.agent_dispatch_allowed is
  'US-13.13: production deployments may be dispatched to an agent only '
  'when a human set this. dev/uat agent dispatch needs no flag; '
  'protected deployments refuse agents regardless.';

create or replace function public.log_deployment_config_change()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_areas jsonb := '[]'::jsonb;
  v_detail jsonb := '{}'::jsonb;
  v_actor text := coalesce(nullif(auth.jwt() ->> 'email', ''), 'api');
begin
  if tg_op = 'INSERT' then
    insert into public.deployment_events (org_id, deployment_id, actor, event, areas)
    values (new.org_id, new.id, v_actor, 'created', '["definition"]'::jsonb);
    return null;
  end if;

  if new.name is distinct from old.name
     or new.branch is distinct from old.branch
     or new.server_id is distinct from old.server_id
     or new.target_folder is distinct from old.target_folder
     or new.run_timeout_minutes is distinct from old.run_timeout_minutes then
    v_areas := v_areas || '["definition"]'::jsonb;
  end if;
  if new.script is distinct from old.script then
    v_areas := v_areas || '["script"]'::jsonb;
    v_detail := v_detail || jsonb_build_object('previous_script', old.script);
  end if;
  if new.source_folder is distinct from old.source_folder
     or new.exclude_patterns is distinct from old.exclude_patterns then
    v_areas := v_areas || '["source-filters"]'::jsonb;
  end if;
  if new.strategy is distinct from old.strategy
     or new.keep_releases is distinct from old.keep_releases then
    v_areas := v_areas || '["strategy"]'::jsonb;
  end if;
  if new.health_check_url is distinct from old.health_check_url
     or new.health_check_expected_status is distinct from old.health_check_expected_status
     or new.health_check_window_seconds is distinct from old.health_check_window_seconds
     or new.health_check_initial_delay_seconds is distinct from old.health_check_initial_delay_seconds then
    v_areas := v_areas || '["health-check"]'::jsonb;
  end if;
  if new.protected is distinct from old.protected then
    v_areas := v_areas || '["protection"]'::jsonb;
  end if;
  -- US-13.13: flipping agent dispatch is a policy change — audited like
  -- protection.
  if new.agent_dispatch_allowed is distinct from old.agent_dispatch_allowed then
    v_areas := v_areas || '["agent-dispatch"]'::jsonb;
    v_detail := v_detail || jsonb_build_object(
      'agent_dispatch_allowed', new.agent_dispatch_allowed);
  end if;

  if v_areas = '[]'::jsonb then
    return null;
  end if;

  insert into public.deployment_events
    (org_id, deployment_id, actor, event, areas, detail)
  values (new.org_id, new.id, v_actor, 'updated', v_areas, v_detail);
  return null;
end;
$$;

alter table public.worker_instructions
  drop constraint worker_instructions_run_kind_check;
alter table public.worker_instructions
  add constraint worker_instructions_run_kind_check
  check (run_kind in ('prd', 'plan', 'code', 'release', 'breakdown', 'test',
                      'deploy'));

-- baked_worker_instruction v-next: 112's six texts verbatim + 'deploy'.
create or replace function public.baked_worker_instruction(p_kind text)
returns text
language sql
immutable
as $$
  select case p_kind
    when 'prd' then
      'Write a product requirements document for this feature from the raw '
      || 'idea and context provided. Produce exactly these four markdown '
      || 'sections, in this order: ## Problem, ## Goals, ## Out of scope, '
      || '## Acceptance criteria. Be concrete and testable in the '
      || 'acceptance criteria; keep scope honest — anything doubtful goes '
      || 'to Out of scope. If this is a redraft, address the send-back '
      || 'feedback directly instead of starting over.'
    when 'breakdown' then
      'Break the approved PRD into engineering stories. Study the '
      || 'repository first over MCP (get_repo_tree, read_repo_file) and '
      || 'read the project guidelines and learnings, so the split fits the '
      || 'actual codebase. Produce self-contained stories, each with a '
      || 'title, a story body, and concrete acceptance criteria, ordered by '
      || 'dependency. Honor the breakdown mode and the manager''s '
      || 'instructions in the context: ''single'' means exactly one story '
      || 'covering the whole PRD; ''multiple'' means a detailed split. Hand '
      || 'the split back with submit_stories — the factory creates the '
      || 'child stories as drafts for the manager to curate.'
    when 'plan' then
      'Study the repository first, then produce a plan — not code. Read it '
      || 'over MCP with get_repo_tree and read_repo_file; no clone is '
      || 'needed. If the repo carries docs/factory/INDEX.md, read the '
      || 'index and the stories that precede yours in the same feature '
      || 'before designing — the decisions your predecessors made are '
      || 'recorded there, not just implied by their code. '
      || 'Do not modify any project file. Write an implementation '
      || 'plan (approach, files to touch, risks) and a test plan (how the '
      || 'change will be verified). Propose concrete test cases where '
      || 'useful. Honor the acceptance criteria and the PRD context when '
      || 'present; if this is a re-plan, address the send-back feedback. '
      || 'Do not write exit criteria that require RUNNING a suite (e.g. '
      || '"pytest green", "npm test passes") — you cannot know whether the '
      || 'worker that picks up the code run has an environment to run it '
      || 'in. State the bar as tests authored and validate_submission '
      || 'clean, and leave execution to whoever can actually observe it. '
      || 'Narrate as you go: call report_progress with a short real note '
      || 'at meaningful boundaries — after claiming, when you start '
      || 'writing, when a major piece lands — so the manager can tell '
      || 'working from frozen. A note also extends your lease.'
    when 'code' then
      'Implement the change honoring the approved implementation plan and '
      || 'the acceptance criteria. Follow the project guidelines and '
      || 'learnings. If the repo carries docs/factory/INDEX.md, read the '
      || 'index and the preceding stories in your feature before designing '
      || 'anything — earlier decisions live there. '
      || 'Keep the diff focused — no drive-by refactors. Hand '
      || 'back over MCP unless you have git tooling: get_workspace pins a '
      || 'base_sha, work on the extracted tree, submit_changeset declares '
      || 'that base_sha. Git-capable workers may instead clone the factory '
      || 'remote, push the run''s branch, and submit_code_work. '
      || 'On tests: writing them is always part of the work; RUNNING them '
      || 'depends on your environment. If you can execute the suite, do, '
      || 'and report_test_results against the run context''s test case '
      || 'ids. If you cannot, submit anyway and report nothing — never '
      || 'report a result you did not observe, and never stall the run '
      || 'waiting for an ability you do not have. Unreported cases stay '
      || 'unrun and the manager sees that honestly. Use blocked (with '
      || 'evidence) only for a case someone looked at and could not run. '
      || 'If this is a retry, address the rejection feedback directly. '
      || 'Narrate as you go: call report_progress with a short real note '
      || 'at meaningful boundaries — after claiming, before a long write, '
      || 'when a major piece lands, before submitting — so the manager can '
      || 'tell working from frozen. A note also extends your lease.'
    when 'test' then
      'A staffed verification pass over a submitted code run''s branch. '
      || 'Check the branch out read-only through the factory remote (your '
      || 'token is the HTTP Basic password) — a test run never pushes. '
      || 'Apply the build configuration, run the project''s declared '
      || 'commands, and execute the manager''s test cases from the work '
      || 'context. Report per-case outcomes with report_test_results — '
      || 'passed, failed, or blocked (with evidence) — and ONLY what you '
      || 'actually observed. Execution is the work: if you cannot execute '
      || 'anything, release_work with a note saying why instead of '
      || 'completing empty. Fixes are not yours to make — failures flow to '
      || 'the manager''s gate, and the retry is a code run. Finish with '
      || 'submit_test_run and a short summary of what ran and where.'
    when 'release' then
      'Reference material for shipping to UAT or Production — not a run you '
      || 'are dispatched for. Versions are system-computed as '
      || 'V<epic>.<release-seq>: the major is the current epic number, the '
      || 'minor a per-epic release counter (V1.1, V1.2, then V2.1 once the '
      || 'epic rolls). The factory mints and git-tags the version at the '
      || 'release cut — never hand-pick one. When preparing a cut, write '
      || 'release notes that read as a changelog: user-facing changes '
      || 'first, then fixes and internal changes, listing the included work '
      || 'items by their epic-scoped ids. Ship to UAT first, record the QA '
      || 'sign-off, then promote the same version to Production — promotion '
      || 'never re-versions.'
    when 'deploy' then
      'Execute and babysit ONE deployment: the one your claimed run names '
      || '— the trigger tools refuse any other. The server does the '
      || 'deploying; your job is trigger, observe, verify, report. '
      || 'trigger_deployment starts it; poll get_deployment_run_status '
      || 'until it finishes; then verify with get_deployment_health before '
      || 'declaring anything. Never claim an outcome you did not observe. '
      || 'Rollback happens only when the manager pre-authorized it at '
      || 'dispatch, only once, and only on failed health checks — '
      || 'otherwise report deployed-but-unhealthy and stop. Finish with '
      || 'submit_deploy_run and the honest verdict: deployed, '
      || 'deployed-unhealthy, or rolled-back.'
    else null
  end;
$$;

create or replace function public.seed_worker_instructions()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.worker_instructions (org_id, project_id, run_kind, content)
  select new.org_id, new.id, k.kind, public.default_worker_instruction(k.kind)
  from (values ('prd'), ('breakdown'), ('plan'), ('code'), ('release'),
               ('test'), ('deploy')) as k(kind)
  on conflict (project_id, run_kind) do nothing;
  return new;
end;
$$;

insert into public.worker_instructions (org_id, project_id, run_kind, content)
select p.org_id, p.id, 'deploy', public.default_worker_instruction('deploy')
from public.projects p
on conflict (project_id, run_kind) do nothing;
