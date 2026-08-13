-- 176_elaborate_a_story: US-44.1 — an agent can be asked to flesh out a story.
--
-- A story's text is written once, by the breakdown agent, from the PRD, by
-- something that has never read the repository. Curation moves it to `ready`
-- and changes no text. So the first pass anyone makes over a story with the
-- source open is the PLAN RUN — the most expensive thing in the pipeline, at
-- $5–15 each. When a story is thin, vague, or duplicates the one beside it,
-- that money buys a careful plan for the wrong thing and the manager finds out
-- at the plan gate.
--
-- This adds the cheap pass in front: an `elaborate` run that reads the
-- repository and PROPOSES a rewrite of the story itself. The proposal is an
-- artifact, not an in-place edit — the manager gets a before, an after, and a
-- decision, which is the whole reason the plan and PRD gates exist.

-- ---------------------------------------------------------------------------
-- Vocabulary
-- ---------------------------------------------------------------------------

alter table public.runs drop constraint if exists runs_kind_check;
alter table public.runs
  add constraint runs_kind_check
  check (kind in ('plan', 'code', 'prd', 'breakdown', 'test', 'release',
                  'deploy', 'guidelines', 'elaborate'));

alter table public.worker_instructions
  drop constraint if exists worker_instructions_run_kind_check;
alter table public.worker_instructions
  add constraint worker_instructions_run_kind_check
  check (run_kind in ('prd', 'plan', 'code', 'release', 'breakdown', 'test',
                      'deploy', 'guidelines', 'elaborate'));

-- artifacts.kind's first widening since 031. The existing
-- unique (issue_id, kind, version) versions a re-elaboration for free.
alter table public.artifacts drop constraint if exists artifacts_kind_check;
alter table public.artifacts
  add constraint artifacts_kind_check
  check (kind in ('prd', 'plan', 'test_plan', 'elaboration'));

alter table public.approvals drop constraint if exists approvals_gate_check;
alter table public.approvals
  add constraint approvals_gate_check
  check (gate in ('prd', 'plan', 'code-review', 'qa-signoff',
                  'merge-override', 'promotion', 'elaboration'));

-- ---------------------------------------------------------------------------
-- The hold exemption — NARROWER than us-43.5's, deliberately
-- ---------------------------------------------------------------------------

-- us-15.3 holds any run whose sibling is still `draft`. That is EVERY story in
-- a fresh breakdown set — which is exactly the condition an elaborate run is
-- dispatched to resolve. Without an exemption it would be held, permanently,
-- by the thing it exists to fix.
--
-- But only that rule. An elaboration IS delivery work for its feature, so
-- us-20.5's one-in-flight rule serialising it in sub_no order is correct and
-- it gets no queue_rank privilege. A blanket `return null` like the guidelines
-- kind's would be wrong here, and this comment is why it is not one.
--
-- Surgery over the LIVE body with 172's guard, so the guidelines exemption and
-- every ordering rule are carried forward by construction.
do $migration$
declare
  def text;
  anchor text := E'  if v_issue.parent_id is not null then\n    select count(*) into v_cnt\n';
  replacement text := E'  -- US-44.1: an elaborate run is exempt from THIS rule only. It exists to\n  -- fix the very condition the rule holds on (a fresh breakdown set where\n  -- every sibling is still draft), so holding it here is a deadlock by\n  -- construction. It stays subject to every ordering rule below.\n  if v_issue.parent_id is not null and v_run.kind <> ''elaborate'' then\n    select count(*) into v_cnt\n';
begin
  select prosrc into def
  from pg_proc p
  join pg_namespace n on n.oid = p.pronamespace
  where n.nspname = 'public' and p.proname = 'run_hold_reason';

  if def is null then
    raise exception 'run_hold_reason not found';
  end if;
  if position('<> ''elaborate''' in def) > 0 then
    raise notice '176 is already applied; leaving run_hold_reason alone';
  else
    if position('kind = ''guidelines''' in def) = 0 then
      raise exception
        'the us-43.5 guidelines exemption is missing from run_hold_reason — '
        'migration 172 must land first, or the function has been rebuilt '
        'from an older body (see 095/105/106)';
    end if;
    if (length(def) - length(replace(def, anchor, ''))) / length(anchor) <> 1 then
      raise exception
        'the us-15.3 sibling-draft block is not where 176 expects it — '
        'run_hold_reason has drifted; re-derive this edit from its current '
        'definition rather than replacing it wholesale';
    end if;

    execute
      'create or replace function public.run_hold_reason(p_run uuid) '
      || 'returns text language plpgsql stable as $fn$'
      || replace(def, anchor, replacement)
      || '$fn$';
  end if;
end
$migration$;

-- ---------------------------------------------------------------------------
-- Dispatch
-- ---------------------------------------------------------------------------

create or replace function public.dispatch_elaboration(p_issue uuid)
returns uuid
language plpgsql
as $$
declare
  v_issue public.issues%rowtype;
  v_prior public.artifacts%rowtype;
  v_feedback text;
  v_prd text;
  v_siblings jsonb;
  v_context jsonb;
  v_run uuid;
begin
  select * into v_issue from public.issues where id = p_issue for update;
  if not found then
    raise exception 'issue not found';
  end if;
  if v_issue.abandoned_at is not null then
    raise exception 'issue is abandoned';
  end if;
  -- A feature's requirement is the PRD's job and stays there; this only ever
  -- rewrites a story's own body and criteria.
  if v_issue.type = 'feature' then
    raise exception
      'a feature is elaborated by its PRD, not by this — draft or send back '
      'the PRD instead';
  end if;
  if exists (
    select 1 from public.runs
    where issue_id = p_issue and kind = 'elaborate'
      and status in ('queued', 'running')
  ) then
    raise exception 'an elaboration run for this item is already in flight';
  end if;

  select * into v_prior
  from public.artifacts
  where issue_id = p_issue and kind = 'elaboration'
  order by version desc limit 1;

  if v_prior.id is not null then
    select a.comment into v_feedback
    from public.approvals a
    where a.issue_id = p_issue and a.gate = 'elaboration'
      and a.decision = 'sent-back'
    order by a.created_at desc limit 1;
  end if;

  -- The parent feature's approved PRD: what this story is a slice OF.
  if v_issue.parent_id is not null then
    select content into v_prd
    from public.artifacts
    where issue_id = v_issue.parent_id and kind = 'prd'
      and status = 'approved'
    order by version desc limit 1;

    -- The siblings, so a proposal cannot annex the story next to it. The
    -- split is a contract; an elaboration stays inside its own slice.
    select jsonb_agg(
             jsonb_build_object(
               'id', format('%s-%s.%s.%s',
                 case sib.type when 'bug' then 'BUG'
                               when 'chore' then 'CHORE'
                               else 'US' end,
                 ep.number, sib.item_no, sib.sub_no),
               'title', sib.title,
               'body', sib.body)
             order by sib.sub_no)
      into v_siblings
    from public.issues sib
    left join public.epics ep on ep.id = sib.epic_id
    where sib.parent_id = v_issue.parent_id
      and sib.abandoned_at is null
      and sib.id <> p_issue;
  end if;

  v_context := jsonb_build_object(
    'run_kind', 'elaborate',
    'title', v_issue.title,
    'type', v_issue.type,
    'story', v_issue.body,
    'body', v_issue.body,
    'acceptance_criteria', v_issue.acceptance_criteria,
    'guidelines', public.assemble_project_guidelines(v_issue.project_id),
    'learnings', public.assemble_project_learnings(v_issue.project_id)
  );
  if v_prd is not null then
    v_context := v_context || jsonb_build_object('feature_prd', v_prd);
  end if;
  if v_siblings is not null then
    v_context := v_context || jsonb_build_object('sibling_stories', v_siblings);
  end if;
  if v_prior.id is not null then
    v_context := v_context
      || jsonb_build_object('previous_elaboration', v_prior.content);
  end if;
  if v_feedback is not null then
    v_context := v_context || jsonb_build_object('feedback', v_feedback);
  end if;

  perform public.seed_issue_instructions(p_issue, 'elaborate');

  -- issues.status is deliberately NOT touched, exactly as prd and breakdown
  -- dispatch leave it. prev_issue_status therefore equals the current status.
  insert into public.runs
    (org_id, issue_id, provider, status, kind, input_context, prev_issue_status)
  values
    (v_issue.org_id, p_issue, 'claude', 'queued', 'elaborate', v_context,
     v_issue.status)
  returning id into v_run;

  insert into public.issue_events (org_id, issue_id, type, payload)
  values (v_issue.org_id, p_issue, 'elaboration-dispatched',
          jsonb_build_object('run_id', v_run, 'from_status', v_issue.status));

  return v_run;
end;
$$;

-- ---------------------------------------------------------------------------
-- The seeded instruction — surgery, per 171's header
-- ---------------------------------------------------------------------------

do $migration$
declare
  def text;
  anchor text := E'    else null\n';
  branch text := $branch$    when 'elaborate' then
      'Rewrite ONE story so it is worth planning. It was written from a PRD '
      || 'by an agent that had never read this repository; you have the '
      || 'repository, and the plan run that follows you costs far more than '
      || 'you do. Read first: get_repo_tree and read_repo_file, plus the '
      || 'project guidelines and learnings in your context. '
      || 'Then propose a story body and acceptance criteria. Name REAL '
      || 'files, symbols, routes and tables — a plausible-sounding name is '
      || 'worse than none, because the next agent cannot tell them apart. '
      || 'Keep every acceptance criterion independently checkable: one '
      || 'observable outcome each, no compound criteria. '
      || 'PRESERVE the manager''s own wording wherever it is still right. '
      || 'You are sharpening a story, not restating it in your own voice, '
      || 'and a rewrite that only changes the phrasing wastes the gate. '
      || 'STAY INSIDE THIS STORY''S SLICE. The sibling stories are in your '
      || 'context so you can see the seams: do not absorb work that belongs '
      || 'to one of them, do not propose splitting this story in two, and '
      || 'do not rename or re-parent anything. Only the body and the '
      || 'acceptance criteria are yours to change. '
      || 'If the story is already fine, say so and propose nothing — that '
      || 'is a real answer and it costs the manager one glance. Anything '
      || 'you could not settle from the repository goes in open_questions '
      || 'rather than into a guess buried in the text. '
      || 'Finish with submit_elaboration. Nothing you write is applied: the '
      || 'manager reads your proposal beside the current text and decides.'
$branch$;
begin
  select prosrc into def
  from pg_proc p
  join pg_namespace n on n.oid = p.pronamespace
  where n.nspname = 'public' and p.proname = 'baked_worker_instruction';

  if def is null then
    raise exception 'baked_worker_instruction not found';
  end if;
  if position('when ''elaborate'' then' in def) > 0 then
    raise notice 'the elaborate case is already present; leaving it alone';
    return;
  end if;
  if (length(def) - length(replace(def, anchor, ''))) / length(anchor) <> 1 then
    raise exception
      'the else-null tail is not where 176 expects it — '
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
               ('test'), ('deploy'), ('guidelines'), ('elaborate')) as k(kind)
  on conflict (project_id, run_kind) do nothing;
  return new;
end;
$$;

insert into public.worker_instructions (org_id, project_id, run_kind, content)
select p.org_id, p.id, 'elaborate',
       public.default_worker_instruction('elaborate')
from public.projects p
on conflict (project_id, run_kind) do nothing;
