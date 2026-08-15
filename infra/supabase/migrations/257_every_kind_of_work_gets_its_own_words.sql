-- 257_every_kind_of_work_gets_its_own_words (us-96.3): a standalone story
-- is planned and built on its own premise.
--
-- A story with no parent feature has no PRD behind it and no breakdown that
-- decided its shape — yet its worker reads instructions written for a
-- feature child ("honor the PRD context", "read the stories that precede
-- yours in the same feature"). The standalone pair states the honest
-- premise: the story and its acceptance criteria are the whole contract,
-- and doubt narrows scope rather than inventing it.
--
-- Same construction as 256: check widened, baked defaults appended by the
-- 187-style surgery with a hash guard, instruction_kind_for extended (the
-- one mapping both call sites read), seed + backfill. The editor-side
-- naming of the whole family lands in the same story
-- (worker-instructions-tab.tsx).

-- 1 ------------------------------------------------------------- the kinds
alter table public.worker_instructions
  drop constraint if exists worker_instructions_run_kind_check;
alter table public.worker_instructions
  add constraint worker_instructions_run_kind_check
  check (run_kind in (
    'prd', 'plan', 'code', 'release', 'breakdown', 'test', 'deploy',
    'guidelines', 'elaborate', 'wireframe',
    'story_breakdown', 'test_case_elaborate', 'deploy_script_generate',
    'chore', 'bug_rca', 'bug_fix', 'standalone_plan', 'standalone_code'
  ));

-- 2 ------------------------------------------- the baked defaults, appended
do $migration$
declare
  def text;
  before_hash text;
  after_hash text;
  tail constant text := 'else null';
  addition constant text := $add$when 'standalone_plan' then
      'Study the repository first, then produce a plan — not code. Read it '
      || 'over MCP with get_repo_tree and read_repo_file; no clone is '
      || 'needed. This is a STANDALONE story: no PRD stands behind it and '
      || 'no breakdown decided its shape — the story body and its '
      || 'acceptance criteria are the whole contract. If they leave a real '
      || 'question open, surface it under Risks rather than inventing '
      || 'scope; doubt narrows a standalone story, never widens it. The '
      || 'approved work catalog is in the repo under docs/factory/ — read '
      || 'index.json for what exists and in what order. Do not modify any '
      || 'project file. '
      || 'Write the implementation plan in EXACTLY four sections. '
      || '**What changes** — bullets naming the outcome: what a user or a '
      || 'caller can do afterward that they could not before. '
      || '**Surfaces touched** — the AREAS the change lands in, one line '
      || 'each, no justification. These are areas, NOT file paths. '
      || '**Risks** — what could go wrong, what it would break, and any '
      || 'question the story leaves open. '
      || '**Dependencies** — what must be true, or must land first. '
      || 'Do NOT enumerate file paths anywhere in the plan — the agent '
      || 'that codes this holds the working tree and chooses the files. '
      || 'Also write a test plan — but read this carefully, because '
      || 'approving it CREATES ROWS: every case you write becomes a test '
      || 'case in the manager''s library for a person to walk by hand. '
      || 'Write THREE TO SIX acceptance-level cases in the ```json fence, '
      || 'each phrased as something a person can observe. Fewer is legal. '
      || 'Do not write exit criteria that require RUNNING a suite — state '
      || 'the bar as tests authored and validate_submission clean. '
      || 'If this is a re-plan, address the send-back feedback. Narrate as '
      || 'you go with report_progress; a note also extends your lease.'
    when 'standalone_code' then
      'Implement the change honoring the approved implementation plan and '
      || 'the acceptance criteria. This is a STANDALONE story: no PRD and '
      || 'no sibling stories stand behind it — the story, its acceptance '
      || 'criteria and the approved plan are the whole contract, so do not '
      || 'go looking for a feature context that does not exist, and keep '
      || 'the diff inside this story''s slice. The plan says WHAT changes; '
      || 'choosing the files, the structure and the tests is yours. The '
      || 'docs tree is already in your workspace — docs/factory/ is a '
      || 'local directory; read index.json for what exists. Follow the '
      || 'project guidelines and learnings. Keep the diff focused — no '
      || 'drive-by refactors. Hand back over MCP unless you have git '
      || 'tooling: get_workspace pins a base_sha, work on the extracted '
      || 'tree, submit_changeset declares that base_sha. Git-capable '
      || 'workers may instead clone the factory remote, push the run''s '
      || 'branch, and submit_code_work. On tests: writing them is always '
      || 'part of the work; RUNNING them depends on your environment. If '
      || 'you can execute the suite, do, and report_test_results against '
      || 'the run context''s test case ids; if you cannot, submit anyway '
      || 'and report nothing — never report a result you did not observe. '
      || 'If this is a retry, address the rejection feedback directly. '
      || 'Narrate as you go with report_progress; a note also extends '
      || 'your lease.'
    else null$add$;
begin
  select p.prosrc into def
  from pg_proc p join pg_namespace n on n.oid = p.pronamespace
  where n.nspname = 'public' and p.proname = 'baked_worker_instruction';
  if def is null then
    raise exception 'baked_worker_instruction not found';
  end if;
  if position('standalone_plan' in def) > 0 then
    raise notice 'standalone kinds already present — skipping the append';
    return;
  end if;

  select md5(string_agg(k || ':' || coalesce(public.baked_worker_instruction(k), ''),
                        '|' order by k))
    into before_hash
  from unnest(array['prd', 'breakdown', 'plan', 'code', 'test', 'release',
                    'deploy', 'guidelines', 'elaborate', 'wireframe',
                    'chore', 'bug_rca', 'bug_fix']) as k;

  execute
    'create or replace function public.baked_worker_instruction(p_kind text) '
    || 'returns text language sql immutable as $fn$'
    || replace(def, tail, addition)
    || '$fn$';

  select md5(string_agg(k || ':' || coalesce(public.baked_worker_instruction(k), ''),
                        '|' order by k))
    into after_hash
  from unnest(array['prd', 'breakdown', 'plan', 'code', 'test', 'release',
                    'deploy', 'guidelines', 'elaborate', 'wireframe',
                    'chore', 'bug_rca', 'bug_fix']) as k;

  if before_hash is distinct from after_hash then
    raise exception
      'a pre-existing kind changed (% -> %) — the append reached further '
      'than the terminal else; the transaction is rolled back',
      before_hash, after_hash;
  end if;
end
$migration$;

-- 3 ------------------------------------------------- the mapping, complete
create or replace function public.instruction_kind_for(p_issue uuid, p_run_kind text)
returns text
language sql
stable
as $function$
  select case
    when p_issue is null or p_run_kind not in ('plan', 'code') then p_run_kind
    else coalesce((
      select case
        when i.type = 'chore' and p_run_kind = 'code' then 'chore'
        when i.type = 'bug' and p_run_kind = 'plan' then 'bug_rca'
        when i.type = 'bug' and p_run_kind = 'code' then 'bug_fix'
        when i.type = 'story' and i.parent_id is null and p_run_kind = 'plan'
          then 'standalone_plan'
        when i.type = 'story' and i.parent_id is null and p_run_kind = 'code'
          then 'standalone_code'
        else p_run_kind
      end
      from public.issues i
      where i.id = p_issue
    ), p_run_kind)
  end;
$function$;

comment on function public.instruction_kind_for(uuid, text) is
  'Which worker_instructions row a run of this kind on this issue reads. '
  'us-96.1: a chore''s code run reads ''chore''. us-96.2: a bug''s plan run '
  'reads ''bug_rca'', its code run ''bug_fix''. us-96.3: a standalone '
  'story (no parent feature) reads standalone_plan/standalone_code. A '
  'feature-child story keeps plan/code. Identity for everything else.';

-- 4 ------------------------------------------------------ seed + backfill
create or replace function public.seed_worker_instructions()
returns trigger
language plpgsql
security definer
set search_path = public
as $function$
begin
  insert into public.worker_instructions (org_id, project_id, run_kind, content)
  select
    new.org_id, new.id, k.kind,
    coalesce(
      (
        select s.content from public.org_project_template_sections s
        where s.org_template_id = new.org_template_id
          and s.section_type = 'worker_instruction'
          and s.section_key = k.kind
      ),
      public.default_worker_instruction(k.kind),
      ''
    )
  from (values
    ('prd'), ('plan'), ('code'), ('release'), ('breakdown'), ('test'),
    ('deploy'), ('guidelines'), ('elaborate'), ('wireframe'), ('chore'),
    ('bug_rca'), ('bug_fix'), ('standalone_plan'), ('standalone_code')
  ) as k(kind)
  on conflict (project_id, run_kind) do nothing;

  insert into public.worker_instructions (org_id, project_id, run_kind, content)
  select
    new.org_id, new.id, k.kind,
    coalesce(
      (
        select s.content from public.org_project_template_sections s
        where s.org_template_id = new.org_template_id
          and s.section_type = 'prompt'
          and s.section_key = k.kind
      ),
      ''
    )
  from (values ('test_case_elaborate'), ('deploy_script_generate')) as k(kind)
  on conflict (project_id, run_kind) do nothing;

  return new;
end;
$function$;

insert into public.worker_instructions (org_id, project_id, run_kind, content)
select p.org_id, p.id, k.kind, public.default_worker_instruction(k.kind)
from public.projects p
cross join (values ('standalone_plan'), ('standalone_code')) as k(kind)
on conflict (project_id, run_kind) do nothing;
