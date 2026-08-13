-- 171_guidelines_refresh: US-43.1 — an agent can be asked to write the
-- guidelines.
--
-- A guidelines refresh is a run dispatched FOR the purpose of reading the
-- source and the delivery history and proposing the project's guidelines.
-- us-1.52 drafts sections from a conversation and cannot read the repo;
-- us-5.32 lets a working agent flag ONE section it tripped over. Neither can
-- be asked for a pass.
--
-- Three pieces here:
--   * a `guidelines` run kind (runs.kind, worker_instructions.run_kind) —
--     issue-scoped like plan/code so the work is a visible chore, but it
--     never opens a PR and never walks the plan -> code ladder;
--   * `guideline_refreshes`, a thin parent that groups one pass's proposals
--     into a bundle the manager reviews as a document;
--   * `guideline_recommendations.refresh_id` — nullable, so every existing
--     us-5.32 row keeps behaving exactly as it does today.
--
-- NOTE ON THE LIVE HISTORY: the production project also carries a migration
-- named `guidelines_instruction_quote_fix` with no file here. It repaired a
-- FIRST version of this file that doubled the apostrophes in the branch below
-- twice ("project''s" in the rendered instruction). This file is the repaired
-- version, so a fresh apply needs no such fix and none is checked in. Both
-- projects were verified to agree on the resulting function body afterward.
--
-- Deliberately NOT here: a second write path into project_guidelines.
-- `decide_guideline_recommendation` (069) already applies proposed text under
-- the caller's RLS with the us-5.33 content_audit triggers attributing the
-- change to the deciding manager. Bundled rows go through it unchanged.

-- ---------------------------------------------------------------------------
-- The bundle
-- ---------------------------------------------------------------------------

create table if not exists public.guideline_refreshes (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  issue_id uuid references public.issues(id) on delete set null,
  run_id uuid references public.runs(id) on delete set null,
  worker_id uuid references public.workers(id) on delete set null,
  summary text not null default '',
  scope text not null default 'all' check (scope in ('all', 'existing')),
  focus text not null default '',
  status text not null default 'pending' check (
    status in ('pending', 'decided')
  ),
  created_at timestamptz not null default now(),
  decided_at timestamptz
);

-- One open refresh per project: the us-43.2 trigger refuses a second while
-- one is pending, and a partial unique index is what makes that a race-free
-- promise rather than a check-then-insert.
create unique index if not exists guideline_refreshes_one_open_idx
  on public.guideline_refreshes (project_id)
  where status = 'pending';

create index if not exists guideline_refreshes_project_idx
  on public.guideline_refreshes (project_id, created_at desc);

alter table public.guideline_refreshes enable row level security;

create policy "members read their org guideline refreshes"
  on public.guideline_refreshes for select
  using (public.is_org_member(org_id));

-- No insert/update policy: refreshes are created by the API's service
-- connection (the dispatch endpoint) and settled by the trigger in 173.
-- Members decide the RECOMMENDATIONS, which is where their authority lies.

alter table public.guideline_recommendations
  add column if not exists refresh_id uuid
    references public.guideline_refreshes(id) on delete cascade;

create index if not exists guideline_recommendations_refresh_idx
  on public.guideline_recommendations (refresh_id)
  where refresh_id is not null;

comment on column public.guideline_recommendations.refresh_id is
  'US-43.1: the guidelines-refresh bundle this proposal belongs to. NULL is '
  'a us-5.32 ad-hoc recommendation from a working agent, which keeps its own '
  'Things to Do card; non-NULL rows are reviewed together as one document.';

-- ---------------------------------------------------------------------------
-- The run kind
-- ---------------------------------------------------------------------------

-- runs.kind, last widened by 114 for 'deploy'. The issue_id check is left
-- exactly as it is: a guidelines run always carries its chore, so it is not
-- one of the project-scoped kinds.
alter table public.runs drop constraint if exists runs_kind_check;
alter table public.runs
  add constraint runs_kind_check
  check (kind in ('plan', 'code', 'prd', 'breakdown', 'test', 'release',
                  'deploy', 'guidelines'));

alter table public.worker_instructions
  drop constraint if exists worker_instructions_run_kind_check;
alter table public.worker_instructions
  add constraint worker_instructions_run_kind_check
  check (run_kind in ('prd', 'plan', 'code', 'release', 'breakdown', 'test',
                      'deploy', 'guidelines'));

-- ---------------------------------------------------------------------------
-- The seeded instruction
-- ---------------------------------------------------------------------------

-- The seeded instruction — SURGERY, NOT A REWRITE.
--
-- baked_worker_instruction is NOT 114's body any more. 131 replaced the
-- release case wholesale and 136 rewrote the docs-tree sentences in the plan
-- and code cases, both as in-place patches over pg_proc. A
-- `create or replace` retyped from 114 reverts them — which is the 095/105/106
-- lesson for the third time, and this migration nearly repeated it.
--
-- So: read the CURRENT definition, insert one new `when` branch immediately
-- before `else null`, and RAISE if the anchor is not exactly where expected.
-- A drifted function fails loudly instead of being quietly replaced.

do $migration$
declare
  def text;
  anchor text := E'    else null
';
  branch text := $branch$    when 'guidelines' then
      'Write this project''s guidelines from what is actually in the '
      || 'repository — not from convention, and not from what the existing '
      || 'guidelines already claim. READ FIRST, WRITE SECOND. Study the '
      || 'source over MCP (get_repo_tree, read_repo_file) or from the '
      || 'workspace: manifests and lockfiles for the real stack and its '
      || 'versions, the scripts that actually exist in package.json, '
      || 'Makefile, pyproject or CI for the commands, the test setup, and '
      || 'the CI workflows, container files and infra directories for how '
      || 'it ships. The work-item digest in your context is the delivery '
      || 'history — what was built, what broke, what was abandoned; it is '
      || 'where the footguns come from. '
      || 'Propose a section ONLY where the repository supports it: a '
      || 'single-package repo gets no monorepo notes, and a guess is worse '
      || 'than an omission because the next agent cannot tell them apart. '
      || 'Ground every command you write in a script that exists — never a '
      || 'plausible one. Where a section is already right, leave its text '
      || 'alone rather than rewriting it to sound different. '
      || 'ALWAYS propose the Deployment and Release section (section_key '
      || '''deployment''), even when the evidence is thin — say what you '
      || 'looked for and did not find, and name the file behind every '
      || 'claim you do make. It is prose describing how this project '
      || 'ships, not a script. '
      || 'Honor the scope and the focus note you were dispatched with: '
      || 'existing-sections-only means propose nothing that is not already '
      || 'there, EXCEPT the deployment section. '
      || 'Hand the whole pass back in ONE call to '
      || 'submit_guidelines_refresh — the manager reviews it as a single '
      || 'document and accepts it section by section. Nothing you write is '
      || 'applied automatically. Narrate as you go with report_progress so '
      || 'the manager can tell working from frozen; a note also extends '
      || 'your lease.'
$branch$;
begin
  select prosrc into def
  from pg_proc p
  join pg_namespace n on n.oid = p.pronamespace
  where n.nspname = 'public' and p.proname = 'baked_worker_instruction';

  if def is null then
    raise exception 'baked_worker_instruction not found';
  end if;

  if position('when ''guidelines'' then' in def) > 0 then
    raise notice 'the guidelines case is already present; leaving it alone';
    return;
  end if;

  if (length(def) - length(replace(def, anchor, ''))) / length(anchor) <> 1 then
    raise exception
      'the else-null tail is not where 171 expects it — '
      'baked_worker_instruction has drifted; re-derive this insertion from '
      'its current definition rather than replacing it wholesale';
  end if;

  execute
    'create or replace function public.baked_worker_instruction(p_kind text) '
    || 'returns text language sql immutable as $fn$'
    || replace(def, anchor, branch || anchor)
    || '$fn$';
end
$migration$;

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
               ('test'), ('deploy'), ('guidelines')) as k(kind)
  on conflict (project_id, run_kind) do nothing;
  return new;
end;
$$;

insert into public.worker_instructions (org_id, project_id, run_kind, content)
select p.org_id, p.id, 'guidelines',
       public.default_worker_instruction('guidelines')
from public.projects p
on conflict (project_id, run_kind) do nothing;
