-- 256_a_bug_explains_itself (us-96.2): a bug's think-first phase is a root
-- cause analysis, in plain language.
--
-- Mechanically nothing moves: the run stays kind 'plan', the artifacts stay
-- 'plan'/'test_plan', the statuses stay planning/plan-review/planned, and
-- approve/send-back are untouched. What changes is the words the worker
-- receives: a bug's plan-kind run reads the new 'bug_rca' instruction (five
-- fixed sections, NO diffs or code blocks — the manager judges the fix in
-- words) and its code-kind run reads 'bug_fix' (implement the approved RCA's
-- proposed fix, minimal diff, regression protection). instruction_kind_for
-- (migration 255) gains the two bug branches; both call sites resolve
-- through it already.
--
-- baked_worker_instruction is extended by the 187-style surgical append —
-- the two new WHEN arms land in front of the terminal 'else null' — with a
-- hash guard proving no existing kind's text moved. The full canonical text
-- of the pre-existing kinds lives in 255_chore_is_one_shot.sql.

-- 1 ------------------------------------------------------------- the kinds
alter table public.worker_instructions
  drop constraint if exists worker_instructions_run_kind_check;
alter table public.worker_instructions
  add constraint worker_instructions_run_kind_check
  check (run_kind in (
    'prd', 'plan', 'code', 'release', 'breakdown', 'test', 'deploy',
    'guidelines', 'elaborate', 'wireframe',
    'story_breakdown', 'test_case_elaborate', 'deploy_script_generate',
    'chore', 'bug_rca', 'bug_fix'
  ));

-- 2 ------------------------------------------- the baked defaults, appended
do $migration$
declare
  def text;
  before_hash text;
  after_hash text;
  tail constant text := 'else null';
  addition constant text := $add$when 'bug_rca' then
      'This bug needs a root cause analysis before anyone fixes it. The '
      || 'machinery calls this a plan run, but what you hand back is an '
      || 'RCA, and its reader is a human manager — write for them, not for '
      || 'a compiler. Study first: read the repository over MCP with '
      || 'get_repo_tree and read_repo_file until you can name the cause, '
      || 'not just the symptom. Do not modify any project file. '
      || 'Write the RCA in EXACTLY these five sections. '
      || '## What broke — the symptom in the user''s terms, a sentence or '
      || 'two. ## Root cause — why it happens, in plain language; name '
      || 'files and functions when they anchor the story, but NO diffs, NO '
      || 'patches, NO code blocks — the fix must be judgeable in words. '
      || '## Evidence — what you observed that convicts this cause and '
      || 'rules out its neighbors: the failing path, the log line, the '
      || 'reproduction. ## Proposed fix — what will change and why that '
      || 'closes the cause, still in words. ## Blast radius — what else '
      || 'touches the broken piece and could be affected by the fix. '
      || 'Also write a test plan whose FIRST case is the reproduction: the '
      || 'steps that show the bug today, phrased so a person can run them '
      || 'after the fix and watch it not happen. Approving this RCA turns '
      || 'those cases into rows in the manager''s test library. Keep it to '
      || 'three cases or fewer unless the blast radius demands more. '
      || 'If this is a re-run, address the send-back feedback directly. '
      || 'Narrate as you go with report_progress; a note also extends your '
      || 'lease.'
    when 'bug_fix' then
      'Fix this bug by implementing the approved root cause analysis''s '
      || 'Proposed fix — the RCA rides in your context where a plan '
      || 'normally would. The RCA says WHAT changes, in words; choosing '
      || 'the files, the structure and the tests is yours. Fix the CAUSE '
      || 'the RCA names, not the symptom, and keep the diff minimal — a '
      || 'bug fix never carries a refactor. Protect against regression: '
      || 'the RCA''s test plan leads with the reproduction steps, so make '
      || 'sure the change you hand back would pass them, and add automated '
      || 'coverage where the project''s test setup makes that natural. '
      || 'Follow the project guidelines and learnings. Hand back over MCP '
      || 'unless you have git tooling: get_workspace pins a base_sha, work '
      || 'on the extracted tree, submit_changeset declares that base_sha. '
      || 'Git-capable workers may instead clone the factory remote, push '
      || 'the run''s branch, and submit_code_work. If this is a retry, '
      || 'address the rejection feedback directly. Narrate as you go with '
      || 'report_progress; a note also extends your lease.'
    else null$add$;
begin
  select p.prosrc into def
  from pg_proc p join pg_namespace n on n.oid = p.pronamespace
  where n.nspname = 'public' and p.proname = 'baked_worker_instruction';
  if def is null then
    raise exception 'baked_worker_instruction not found';
  end if;
  if position('bug_rca' in def) > 0 then
    raise notice 'bug kinds already present — skipping the append';
    return;
  end if;

  select md5(string_agg(k || ':' || coalesce(public.baked_worker_instruction(k), ''),
                        '|' order by k))
    into before_hash
  from unnest(array['prd', 'breakdown', 'plan', 'code', 'test', 'release',
                    'deploy', 'guidelines', 'elaborate', 'wireframe',
                    'chore']) as k;

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
                    'chore']) as k;

  if before_hash is distinct from after_hash then
    raise exception
      'a pre-existing kind changed (% -> %) — the append reached further '
      'than the terminal else; the transaction is rolled back',
      before_hash, after_hash;
  end if;
end
$migration$;

-- 3 ------------------------------------------------- the mapping, extended
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
  'reads ''bug_rca'', its code run ''bug_fix''. us-96.3 adds the '
  'standalone-story pair. Identity for everything else.';

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
    ('bug_rca'), ('bug_fix')
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
cross join (values ('bug_rca'), ('bug_fix')) as k(kind)
on conflict (project_id, run_kind) do nothing;
