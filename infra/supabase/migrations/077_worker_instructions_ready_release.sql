-- 077_worker_instructions_ready_release: mark-worker-instructions-ready flag +
-- a fourth "Versioning & Release" instruction block (US-7.5).
--
-- The release block is reference material, NOT a dispatchable run kind — there
-- is no release run. It is served wherever worker instructions are (MCP + work
-- context) via worker_instruction_for(project,'release'). The ready flag is
-- project-level and sticky, mirroring guidelines (us-7.4).

alter table public.projects
  add column worker_instructions_ready_at timestamptz,
  add column worker_instructions_ready_by uuid;

-- Widen the run_kind check to admit 'release'.
alter table public.worker_instructions
  drop constraint worker_instructions_run_kind_check;
alter table public.worker_instructions
  add constraint worker_instructions_run_kind_check
  check (run_kind in ('prd', 'plan', 'code', 'release'));

-- Extend the canonical BAKED defaults with the release block. The us-5.17
-- override wrapper default_worker_instruction() delegates to this, so it must
-- stay untouched (redefining it here would clobber the superadmin override).
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
    when 'plan' then
      'Study the repository first, then produce a plan — not code. Do not '
      || 'modify any project file. Write an implementation plan (approach, '
      || 'files to touch, risks) and a test plan (how the change will be '
      || 'verified). Propose concrete test cases where useful. Honor the '
      || 'acceptance criteria and the PRD context when present; if this is '
      || 'a re-plan, address the send-back feedback.'
    when 'code' then
      'Implement the change honoring the approved implementation plan and '
      || 'the acceptance criteria. Follow the project guidelines and '
      || 'learnings. Keep the diff focused — no drive-by refactors. Note '
      || 'test cases a human should run when submitting. If this is a '
      || 'retry, address the rejection feedback directly.'
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
    else null
  end;
$$;

-- Seed the release row on new projects (idempotent).
create or replace function public.seed_worker_instructions()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.worker_instructions (org_id, project_id, run_kind, content)
  select new.org_id, new.id, k.kind, public.default_worker_instruction(k.kind)
  from (values ('prd'), ('plan'), ('code'), ('release')) as k(kind)
  on conflict (project_id, run_kind) do nothing;
  return new;
end;
$$;

-- Backfill the release row for every existing project (idempotent).
insert into public.worker_instructions (org_id, project_id, run_kind, content)
select p.org_id, p.id, 'release', public.default_worker_instruction('release')
from public.projects p
on conflict (project_id, run_kind) do nothing;
