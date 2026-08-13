-- 052_worker_instructions: editable per-kind worker instructions (US-5.14).
--
-- The behavioral directives a worker receives per run kind (prd/plan/code)
-- become stored, per-project, manager-editable content instead of strings
-- hardcoded in the API. The canonical factory defaults live HERE, in
-- public.default_worker_instruction — the single source used for seeding,
-- blank-content fallback, and the UI's "Reset to default" (via RPC).
-- Correctness-critical mechanics (branch, remote, auth, submit tool) are
-- NOT stored — the API always emits those in code.

create table public.worker_instructions (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  project_id uuid not null,
  run_kind text not null check (run_kind in ('prd', 'plan', 'code')),
  content text not null default '',
  updated_by uuid,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (project_id, run_kind),
  foreign key (project_id, org_id)
    references public.projects (id, org_id) on delete cascade
);

create index worker_instructions_project_idx
  on public.worker_instructions (project_id);

alter table public.worker_instructions enable row level security;

create policy "members manage their org worker instructions"
  on public.worker_instructions for all
  using (public.is_org_member(org_id))
  with check (public.is_org_member(org_id));

create trigger worker_instructions_updated_at
  before update on public.worker_instructions
  for each row execute function public.touch_updated_at();

-- Manager edits stamp the editor; service-role writes (none today) leave
-- updated_by untouched. Seeded rows keep updated_by null = "factory default".
create or replace function public.stamp_worker_instructions_editor()
returns trigger
language plpgsql
as $$
begin
  if auth.uid() is not null then
    new.updated_by := auth.uid();
  end if;
  return new;
end;
$$;

create trigger worker_instructions_editor
  before update on public.worker_instructions
  for each row execute function public.stamp_worker_instructions_editor();

-- ------------------------------------------------ canonical factory defaults
-- The single code location for the default behavioral text per run kind
-- (US-5.14 AC). Matches the directives previously hardcoded across
-- worker.py / factory_mcp.py / the runner prompt.
create or replace function public.default_worker_instruction(p_kind text)
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
    else null
  end;
$$;

-- Live-read composition source: stored content when non-blank, else the
-- factory default. The API calls this at context-serve time.
create or replace function public.worker_instruction_for(p_project uuid, p_kind text)
returns text
language sql
stable
as $$
  select coalesce(
    nullif(trim((
      select wi.content from public.worker_instructions wi
      where wi.project_id = p_project and wi.run_kind = p_kind
    )), ''),
    public.default_worker_instruction(p_kind)
  );
$$;

-- ------------------------------------------------------------------ seeding
create or replace function public.seed_worker_instructions()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.worker_instructions (org_id, project_id, run_kind, content)
  select new.org_id, new.id, k.kind, public.default_worker_instruction(k.kind)
  from (values ('prd'), ('plan'), ('code')) as k(kind)
  on conflict (project_id, run_kind) do nothing;
  return new;
end;
$$;

create trigger projects_seed_worker_instructions
  after insert on public.projects
  for each row execute function public.seed_worker_instructions();

-- Backfill every existing project (idempotent).
insert into public.worker_instructions (org_id, project_id, run_kind, content)
select p.org_id, p.id, k.kind, public.default_worker_instruction(k.kind)
from public.projects p
cross join (values ('prd'), ('plan'), ('code')) as k(kind)
on conflict (project_id, run_kind) do nothing;
