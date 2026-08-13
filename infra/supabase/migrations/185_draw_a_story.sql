-- 185_draw_a_story: US-48.2 — an agent can be asked to draw a story.
--
-- Everything between a story's text and its code is written in words. US-44.1
-- added the cheap pass that grounds those words in the repository; US-45.1
-- then made the plan deliberately coarse ("surfaces touched" names AREAS, not
-- paths). For a UI story the concrete statement of the surface is a picture,
-- and nothing in the pipeline produces one — so the first time anyone sees the
-- screen is the PR, which is the most expensive place to discover it is wrong.
--
-- This adds a `wireframe` run kind that draws it, between elaboration and the
-- plan. It copies US-44.1's shape almost exactly, with two differences that
-- are decisions rather than omissions:
--
--   * NO GATE. A sketch is not a contract. The wireframe lands on hand-back
--     and the manager's lever is Redo with a comment. There is no approvals
--     row and no `approvals.gate` widening, because a fifteen-story fan-out
--     must not put fifteen items in Things to Do.
--   * "NO UI SURFACE" IS A SUCCESS. A fan-out across a feature will hit
--     migrations and capability gates. An agent that has read the repository
--     is far better placed to say a story has no screen than a manager
--     guessing from titles at dispatch time, so that verdict completes the
--     run and writes no file.
--
-- The artifact stores the DECLARATION (the JSON the kit renders), not the
-- rendered HTML. The file in docs/wireframes/ is a rendering of it, which is
-- what lets a kit upgrade restyle every wireframe in a repository without
-- re-running a single agent.

-- ---------------------------------------------------------------------------
-- Vocabulary
-- ---------------------------------------------------------------------------

alter table public.runs drop constraint if exists runs_kind_check;
alter table public.runs
  add constraint runs_kind_check
  check (kind in ('plan', 'code', 'prd', 'breakdown', 'test', 'release',
                  'deploy', 'guidelines', 'elaborate', 'wireframe'));

alter table public.worker_instructions
  drop constraint if exists worker_instructions_run_kind_check;
alter table public.worker_instructions
  add constraint worker_instructions_run_kind_check
  check (run_kind in ('prd', 'plan', 'code', 'release', 'breakdown', 'test',
                      'deploy', 'guidelines', 'elaborate', 'wireframe'));

-- artifacts.kind's second widening, after 176's `elaboration`. The existing
-- unique (issue_id, kind, version) versions a redo for free.
alter table public.artifacts drop constraint if exists artifacts_kind_check;
alter table public.artifacts
  add constraint artifacts_kind_check
  check (kind in ('prd', 'plan', 'test_plan', 'elaboration', 'wireframe'));

-- ---------------------------------------------------------------------------
-- The capability gate
-- ---------------------------------------------------------------------------

-- Migration 178 exists because US-43.1 and US-44.1 both DECIDED to ride the
-- `plan` grant and neither implemented the mapping, leaving two run kinds
-- nobody could claim: the gate asked for a capability the matrix cannot
-- grant, and being fail-closed (US-31.3) it answered no to everyone.
--
-- A wireframe run reads the repository and writes no code, which is what a
-- `plan` grant already means. The matrix vocabulary stays at seven columns.
create or replace function public.run_kind_capability(p_kind text)
returns text
language sql
immutable
as $$
  select case p_kind
    -- Read the repository, write no code: exactly what `plan` grants.
    when 'guidelines' then 'plan'
    when 'elaborate'  then 'plan'
    when 'wireframe'  then 'plan'
    else p_kind
  end;
$$;

comment on function public.run_kind_capability(text) is
  'US-43.1/US-44.1/US-48.2: maps a runs.kind to the '
  'worker_capabilities.capability that gates it. Most kinds are their own '
  'capability; the read-only kinds that the US-13.10 matrix has no column '
  'for ride the `plan` grant rather than widening the matrix.';

-- ---------------------------------------------------------------------------
-- The hold exemption — extending 176's, not adding a second one
-- ---------------------------------------------------------------------------

-- us-15.3 holds any run whose sibling is still `draft`. That is EVERY story in
-- a fresh breakdown set, and a wireframe run is dispatched precisely into that
-- condition — before the plan, often before curation. Without the exemption it
-- would be held by the state it exists to work inside.
--
-- Only that rule, exactly as 176 argued for `elaborate`: a wireframe IS
-- delivery work for its feature, so us-20.5's one-in-flight rule serialising a
-- fan-out in sub_no order is correct and wanted (each run then sees the
-- earlier stories' screens). No queue_rank privilege either.
--
-- Surgery over the LIVE body. NOTE, and this is why it is surgery: prod's
-- run_hold_reason is 5,945 characters and dev's is 7,568 — the two databases
-- do not hold the same function. A wholesale rebuild from this file would
-- silently delete whichever rules the other database has. Extending one
-- condition in place is correct on both.
do $migration$
declare
  def text;
  anchor text := E'v_run.kind <> ''elaborate''';
  replacement text := E'v_run.kind not in (''elaborate'', ''wireframe'')';
begin
  select prosrc into def
  from pg_proc p
  join pg_namespace n on n.oid = p.pronamespace
  where n.nspname = 'public' and p.proname = 'run_hold_reason';

  if def is null then
    raise exception 'run_hold_reason not found';
  end if;
  if position('''wireframe''' in def) > 0 then
    raise notice '185 is already applied; leaving run_hold_reason alone';
  else
    if (length(def) - length(replace(def, anchor, ''))) / length(anchor) <> 1 then
      raise exception
        'the us-44.1 elaborate exemption is not where 185 expects it — '
        'migration 176 must land first, or run_hold_reason has drifted; '
        're-derive this edit from its current definition rather than '
        'replacing it wholesale';
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

create or replace function public.dispatch_wireframe(
  p_issue uuid,
  p_feedback text default null
)
returns uuid
language plpgsql
as $$
declare
  v_issue public.issues%rowtype;
  v_prior public.artifacts%rowtype;
  v_elaboration jsonb;
  v_body text;
  v_criteria jsonb;
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
  -- A feature's screens are its stories' screens. Drawing the feature itself
  -- would produce one picture nobody can act on and no story would own it.
  if v_issue.type = 'feature' then
    raise exception
      'a feature is drawn by drawing its stories — use dispatch_wireframe_batch';
  end if;
  if exists (
    select 1 from public.runs
    where issue_id = p_issue and kind = 'wireframe'
      and status in ('queued', 'running')
  ) then
    raise exception 'a wireframe run for this item is already in flight';
  end if;

  -- The story as it stands. An APPROVED elaboration is the current text —
  -- US-44.1's approve path already wrote it back into issues.body, so this
  -- reads the issue and gets the elaborated wording for free. A still-draft
  -- elaboration is deliberately NOT used: it is a proposal the manager has
  -- not accepted, and drawing it would give it authority through the back
  -- door.
  v_body := v_issue.body;
  v_criteria := v_issue.acceptance_criteria;

  select * into v_prior
  from public.artifacts
  where issue_id = p_issue and kind = 'wireframe'
  order by version desc limit 1;

  if v_issue.parent_id is not null then
    select content into v_prd
    from public.artifacts
    where issue_id = v_issue.parent_id and kind = 'prd'
      and status = 'approved'
    order by version desc limit 1;

    -- The siblings' SCREENS, not their bodies: a feature's stories are slices
    -- of one surface, and two of them proposing two different filter bars is
    -- the failure this context exists to prevent. Only the screen names and
    -- routes travel — the whole declaration would be most of a plan run's
    -- context budget by the fifth story.
    select jsonb_agg(x.entry order by x.sub_no)
      into v_siblings
    from (
      select
        sib.sub_no,
        jsonb_build_object(
          'id', format('%s-%s.%s.%s',
            case sib.type when 'bug' then 'BUG'
                          when 'chore' then 'CHORE'
                          else 'US' end,
            ep.number, sib.item_no, sib.sub_no),
          'title', sib.title,
          'screens', coalesce((
            select jsonb_agg(jsonb_build_object(
                     'name', s->>'name',
                     'route', s->>'route'))
            from jsonb_array_elements(
                   case
                     when jsonb_typeof(art.content::jsonb -> 'screens') = 'array'
                     then art.content::jsonb -> 'screens'
                     else '[]'::jsonb
                   end) as s
          ), '[]'::jsonb)
        ) as entry
      from public.issues sib
      left join public.epics ep on ep.id = sib.epic_id
      join lateral (
        select a.content
        from public.artifacts a
        where a.issue_id = sib.id and a.kind = 'wireframe'
        order by a.version desc limit 1
      ) art on true
      where sib.parent_id = v_issue.parent_id
        and sib.abandoned_at is null
        and sib.id <> p_issue
    ) x;
  end if;

  v_context := jsonb_build_object(
    'run_kind', 'wireframe',
    'title', v_issue.title,
    'type', v_issue.type,
    'story', v_body,
    'body', v_body,
    'acceptance_criteria', v_criteria,
    'guidelines', public.assemble_project_guidelines(v_issue.project_id)
  );
  if v_prd is not null then
    v_context := v_context || jsonb_build_object('feature_prd', v_prd);
  end if;
  if v_siblings is not null then
    v_context := v_context || jsonb_build_object('sibling_wireframes', v_siblings);
  end if;
  if v_prior.id is not null then
    v_context := v_context
      || jsonb_build_object('previous_wireframe', v_prior.content);
  end if;
  if p_feedback is not null and length(trim(p_feedback)) > 0 then
    v_context := v_context || jsonb_build_object('feedback', p_feedback);
  end if;

  perform public.seed_issue_instructions(p_issue, 'wireframe');

  -- issues.status is deliberately NOT touched, exactly as prd, breakdown and
  -- elaborate dispatch leave it.
  insert into public.runs
    (org_id, issue_id, provider, status, kind, input_context, prev_issue_status)
  values
    (v_issue.org_id, p_issue, 'claude', 'queued', 'wireframe', v_context,
     v_issue.status)
  returning id into v_run;

  insert into public.issue_events (org_id, issue_id, type, payload)
  values (v_issue.org_id, p_issue, 'wireframe-dispatched',
          jsonb_build_object('run_id', v_run, 'from_status', v_issue.status,
                             'redo', v_prior.id is not null,
                             'feedback', p_feedback));

  return v_run;
end;
$$;

grant execute on function public.dispatch_wireframe(uuid, text) to authenticated;

-- ---------------------------------------------------------------------------
-- The seeded instruction — surgery, per 171's header
-- ---------------------------------------------------------------------------

do $migration$
declare
  def text;
  anchor text := E'    else null\n';
  branch text := $branch$    when 'wireframe' then
      'Draw ONE story as a screen, before anyone plans it. What you hand '
      || 'back is read by a manager deciding whether this is the right '
      || 'screen, and then by the agent that codes it. '
      || 'READ FIRST. Use get_repo_tree and read_repo_file to find the '
      || 'app''s EXISTING screens and copy their shape: where the page '
      || 'title sits, where actions sit, what a list looks like, what the '
      || 'empty state says. A wireframe that invents a new layout for a '
      || 'screen that already has siblings is wrong even when it is pretty. '
      || 'DECLARE, DO NOT STYLE. Your output is a JSON declaration rendered '
      || 'by the project''s wireframe kit — no HTML, no CSS, no colours, no '
      || 'pixel sizes. The components are named for the app''s own: card, '
      || 'table, page-header, button, badge, status-badge, tabs, dialog, '
      || 'field, empty-state, toast, avatar, separator, skeleton, menu, '
      || 'row, grid, stack, text. Reading your wireframe should tell a '
      || 'coder which component to reach for. '
      || 'COVER THE STATES the story implies — populated, empty, loading, '
      || 'error. Declare them on the screen and the kit renders each one; '
      || 'a table needs no help (it becomes skeleton rows when loading and '
      || 'its own empty state when empty). Most bugs live in the states '
      || 'nobody drew. '
      || 'USE REAL COPY. Real labels, real column headings, the real empty '
      || 'sentence. Never lorem ipsum, never "Button 1". The words are half '
      || 'of what the manager is judging. '
      || 'ANNOTATE. Put "ac": <number> on the region that satisfies each '
      || 'acceptance criterion, so the review is a verification and not an '
      || 'opinion. '
      || 'IF THE STORY HAS NO SCREEN — a migration, a capability gate, a '
      || 'metering fix — hand back no_ui_surface with a reason naming what '
      || 'it changes instead. That is a real answer and the right one; do '
      || 'NOT invent a screen to avoid it. '
      || 'Stay inside this story''s slice: the sibling stories'' screens are '
      || 'in your context so a feature''s screens agree with each other, '
      || 'not so you can draw theirs. '
      || 'Finish with submit_wireframe. There is no approval gate — what you '
      || 'hand back is what the manager reads and what the repository gets, '
      || 'so hand back the screen you would defend.'
$branch$;
begin
  select prosrc into def
  from pg_proc p
  join pg_namespace n on n.oid = p.pronamespace
  where n.nspname = 'public' and p.proname = 'baked_worker_instruction';

  if def is null then
    raise exception 'baked_worker_instruction not found';
  end if;
  if position('when ''wireframe'' then' in def) > 0 then
    raise notice 'the wireframe case is already present; leaving it alone';
    return;
  end if;
  if (length(def) - length(replace(def, anchor, ''))) / length(anchor) <> 1 then
    raise exception
      'the else-null tail is not where 185 expects it — '
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
               ('test'), ('deploy'), ('guidelines'), ('elaborate'),
               ('wireframe')) as k(kind)
  on conflict (project_id, run_kind) do nothing;
  return new;
end;
$$;

insert into public.worker_instructions (org_id, project_id, run_kind, content)
select p.org_id, p.id, 'wireframe',
       public.default_worker_instruction('wireframe')
from public.projects p
on conflict (project_id, run_kind) do nothing;
