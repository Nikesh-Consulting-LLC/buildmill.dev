-- 261_the_factory_learns_a_merge_run (us-98.1): `merge` becomes a run kind.
--
-- Work accumulates on branches faster than it lands, and folding several of
-- them into the default branch has real judgement in it — two agents touched
-- the same file and only a reader who understands both changes can say what
-- the merged file should be. There is no merge, rebase or three-way logic
-- anywhere in the api today; only detection (MergeConflict) and a path that
-- hands the conflict back to an agent. This gives that work a kind.
--
-- A merge run is dispatched on a CHORE and keeps the chore's single-shot
-- shape: there is no planning phase for a merge, exactly as there is none
-- for a chore's build.
--
-- Same construction as 256/257: checks widened, baked defaults appended by
-- the 187-style surgery with a hash guard, seed + backfill.
--
-- instruction_kind_for is DELIBERATELY not touched. It short-circuits on
-- `p_run_kind not in ('plan','code')`, so 'merge' already passes through
-- unchanged. That is easy to break later, so test_instruction_kind_for_sql
-- pins it rather than leaving it to the reader.

-- 1 ------------------------------------------------------------- the kinds
alter table public.runs drop constraint if exists runs_kind_check;
alter table public.runs
  add constraint runs_kind_check
  check (kind in ('plan', 'code', 'prd', 'breakdown', 'test', 'release',
                  'deploy', 'guidelines', 'elaborate', 'wireframe', 'merge'));

alter table public.worker_instructions
  drop constraint if exists worker_instructions_run_kind_check;
alter table public.worker_instructions
  add constraint worker_instructions_run_kind_check
  check (run_kind in (
    'prd', 'plan', 'code', 'release', 'breakdown', 'test', 'deploy',
    'guidelines', 'elaborate', 'wireframe',
    'story_breakdown', 'test_case_elaborate', 'deploy_script_generate',
    'chore', 'bug_rca', 'bug_fix', 'standalone_plan', 'standalone_code',
    'merge'
  ));

-- 2 ------------------------------------------- the baked defaults, appended
do $migration$
declare
  def text;
  before_hash text;
  after_hash text;
  guarded constant text[] := array[
    'prd', 'breakdown', 'plan', 'code', 'test', 'release', 'deploy',
    'guidelines', 'elaborate', 'wireframe', 'chore', 'bug_rca', 'bug_fix',
    'standalone_plan', 'standalone_code'
  ];
  tail constant text := 'else null';
  addition constant text := $add$when 'merge' then
      'You are landing one or more branches onto the base branch. The run '
      || 'context names the base and every branch you must merge, each with '
      || 'the head sha recorded when the work was dispatched. Read them with '
      || 'get_workspace, passing the ref you want — your claim licenses '
      || 'exactly those branches and the base, and nothing else. '
      || 'This is ALL OR NOTHING. Every branch named in the context must be '
      || 'merged, or you submit none of them. There is no partial merge: '
      || 'landing four of six is not progress, it is a state nobody asked '
      || 'for. If a branch defeats you, say so and stop — report the branch, '
      || 'the paths that conflicted, and what you tried. A named failure is '
      || 'a useful answer; a quiet half-merge is not. '
      || 'Resolve every conflict by READING BOTH SIDES. Decide from what the '
      || 'code does, never from a rule about which side wins — "take theirs" '
      || 'and "take ours" are not resolutions, they are coin flips with a '
      || 'changelog. If two changes to the same function are both wanted, '
      || 'the merged file contains both. If they genuinely contradict, that '
      || 'is a branch you could not merge; see above. '
      || 'NEVER drop a change you did not understand. A merged file that '
      || 'compiles and reads cleanly can still have lost a whole function, '
      || 'and nothing downstream will catch it. When you are unsure whether '
      || 'a hunk still belongs, keep it and say so in your summary. '
      || 'Do not reformat, re-order imports, rename, or tidy anything that '
      || 'was not in conflict — a merge diff must be readable as a merge. '
      || 'Build and run whatever the repository''s own checks are before you '
      || 'submit; a merge that does not build is a merge that lost '
      || 'something. Then submit the merged tree with a summary and, for '
      || 'EVERY branch, what happened to it: clean, or reconciled with the '
      || 'decision you made and why. The manager reads that account before '
      || 'the diff — it is the only place a silent loss would show. '
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
  if position('''merge''' in def) > 0 then
    raise notice 'merge kind already present — skipping the append';
    return;
  end if;

  select md5(string_agg(k || ':' || coalesce(public.baked_worker_instruction(k), ''),
                        '|' order by k))
    into before_hash
  from unnest(guarded) as k;

  execute
    'create or replace function public.baked_worker_instruction(p_kind text) '
    || 'returns text language sql immutable as $fn$'
    || replace(def, tail, addition)
    || '$fn$';

  select md5(string_agg(k || ':' || coalesce(public.baked_worker_instruction(k), ''),
                        '|' order by k))
    into after_hash
  from unnest(guarded) as k;

  if before_hash is distinct from after_hash then
    raise exception
      'a pre-existing kind changed (% -> %) — the append reached further '
      'than the terminal else; the transaction is rolled back',
      before_hash, after_hash;
  end if;
end
$migration$;

-- 3 ------------------------------------------------------ seed + backfill
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
    ('bug_rca'), ('bug_fix'), ('standalone_plan'), ('standalone_code'),
    ('merge')
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
select p.org_id, p.id, 'merge', public.default_worker_instruction('merge')
from public.projects p
on conflict (project_id, run_kind) do nothing;
