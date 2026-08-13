-- 187_plan_against_the_wireframe: US-48.4.
--
-- A wireframe that only the manager reads is a nice document. The reason to
-- draw a story before planning it is that the two runs after it — the plan and
-- the code — should be building the thing in the picture. Neither knows it
-- exists: migration 174 left the plan instruction telling the agent to read
-- docs/factory/, and `docs/wireframes/` is not mentioned anywhere. A code run
-- has the file physically on disk from its workspace and is never told to open
-- it. Both would independently re-invent a screen that has already been drawn
-- and reviewed.
--
-- This adds ONE sentence to each branch. Deliberately not a fifth plan
-- section: US-45.1 fixed the plan format at exactly four sections and said in
-- as many words that the plan does not bind the code agent's choice of files,
-- structure or tests. What changes is a constraint on an existing section —
-- "Surfaces touched" must agree with the screens, and a disagreement is named
-- under "Risks" rather than silently designed around. A plan that believes the
-- screen is wrong says so at the cheapest gate in the pipeline.
--
-- A wireframe never blocks a plan. The sentences are conditional on there
-- being one; a story without one plans exactly as it does today.
--
-- SURGERY OVER THE LIVE BODY with 171's drift guard, per that migration's
-- header: baked_worker_instruction has been rebuilt across 057, 066, 077, 085,
-- 095, 105, 106, 108, 110, 112, 114, 131, 133, 136, 171, 174, 175, 176 and
-- 185. Each anchor below was verified to occur exactly once in the live source
-- before this was written, and the insertion goes INSIDE the existing string
-- literal, so no other kind is touched by construction. Apostrophes are
-- avoided in the inserted text rather than escaped — the text is re-parsed as
-- SQL source, and a stray quote there breaks the whole function.

do $migration$
declare
  def text;
  plan_anchor text := 'leave execution to whoever can actually observe it.';
  plan_add text := ' If a Wireframe section is present in your context, this '
    || 'story has already been drawn and reviewed: your Surfaces touched must '
    || 'be consistent with those screens, naming the same surfaces they show. '
    || 'If you believe the screen is wrong, say so under Risks — do not '
    || 'quietly design around it, and do not restate the wireframe as a fifth '
    || 'section.';
  code_anchor text := 'If this is a retry, address the rejection feedback directly.';
  code_add text := ' If a Wireframe section is present in your context, the '
    || 'rendered screen is already in your workspace under docs/wireframes/ — '
    || 'open it and build to it. Where the code has to depart from it, say so '
    || 'in your hand-back notes so the manager learns it at review rather than '
    || 'from a screenshot.';
  before_hash text;
  after_hash text;
begin
  select prosrc into def
  from pg_proc p
  join pg_namespace n on n.oid = p.pronamespace
  where n.nspname = 'public' and p.proname = 'baked_worker_instruction';

  if def is null then
    raise exception 'baked_worker_instruction not found';
  end if;
  if position('Wireframe section is present' in def) > 0 then
    raise notice '187 is already applied; leaving baked_worker_instruction alone';
    return;
  end if;

  if (length(def) - length(replace(def, plan_anchor, ''))) / length(plan_anchor) <> 1
  then
    raise exception
      'the us-45.1 plan tail is not where 187 expects it (expected exactly '
      'one occurrence) — baked_worker_instruction has drifted; re-derive this '
      'insertion from its current definition rather than replacing it wholesale';
  end if;
  if (length(def) - length(replace(def, code_anchor, ''))) / length(code_anchor) <> 1
  then
    raise exception
      'the code retry sentence is not where 187 expects it (expected exactly '
      'one occurrence) — baked_worker_instruction has drifted; re-derive this '
      'insertion from its current definition rather than replacing it wholesale';
  end if;

  -- What every OTHER kind reads, before and after. US-45.1 verified its own
  -- "byte-identical" claim by hashing rather than by reading the diff; this
  -- does the same, and refuses rather than reporting.
  select md5(string_agg(k || ':' || coalesce(public.baked_worker_instruction(k), ''),
                        '|' order by k))
    into before_hash
  from unnest(array['prd', 'breakdown', 'test', 'release', 'deploy',
                    'guidelines', 'elaborate', 'wireframe']) as k;

  execute
    'create or replace function public.baked_worker_instruction(p_kind text) '
    || 'returns text language sql immutable as $fn$'
    || replace(
         replace(def, plan_anchor, plan_anchor || plan_add),
         code_anchor, code_anchor || code_add)
    || '$fn$';

  select md5(string_agg(k || ':' || coalesce(public.baked_worker_instruction(k), ''),
                        '|' order by k))
    into after_hash
  from unnest(array['prd', 'breakdown', 'test', 'release', 'deploy',
                    'guidelines', 'elaborate', 'wireframe']) as k;

  if before_hash is distinct from after_hash then
    raise exception
      'a kind other than plan/code changed (% -> %) — the surgery reached '
      'further than its two anchors; the transaction is rolled back',
      before_hash, after_hash;
  end if;
end
$migration$;
