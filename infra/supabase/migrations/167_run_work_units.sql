-- US-39.2 — a batch run gets an allowance for the work it carries.
--
-- In feature/epic build mode a feature's coding phase is ONE run over every
-- story under it (migration 139, us-22.9). Its time came from a flat per-agent
-- `max_run_minutes` and its turns from a flat per-run-kind `max_turns`. Neither
-- knows how much work the run carries, so eight stories got the same allowance
-- as one.
--
-- Turns bit first, being the tighter of the two. Observed 2026-07-27:
--
--   the claude CLI exited 1 after 851s. Its last output:
--   Error: Reached max turns (40)
--
-- -- well inside its time, out of turns, having done a fraction of the work.
-- And because turn exhaustion is a plain non-zero exit, the repair loop
-- retried it into exactly the same wall and spent an item attempt doing so.

-- How much work is in this run --------------------------------------------
--
-- One function, used by BOTH the time and the turn calculation. They are two
-- symptoms of the same missing idea, and counting twice is how they drift.
create or replace function public.run_work_units(p_run uuid)
returns int
language sql
stable
set search_path = ''
as $$
  select greatest(1, coalesce((
    select count(*)
    from public.issues c
    join public.issues f on f.id = c.parent_id
    join public.runs r    on r.issue_id = f.id
    join public.projects p on p.id = f.project_id
    where r.id = p_run
      -- Only the batch shape carries more than one unit: a feature's own code
      -- run, in a build mode where the feature owns the build (us-22.9 /
      -- migration 137). Every other run -- a story's code run, a plan, a prd,
      -- a test, a deploy, a release -- is one unit, which is why this change
      -- is invisible to any project that does not batch.
      and r.kind = 'code'
      and f.type in ('feature', 'epic')
      and coalesce(p.build_mode, 'story') in ('feature', 'epic')
      and c.abandoned_at is null
  ), 1))::int;
$$;

comment on function public.run_work_units(uuid) is
  'US-39.2: how many stories a run is actually carrying. The non-abandoned '
  'children for a feature/epic batch code run; 1 for everything else. Time and '
  'turns are both scaled by this one number so they cannot drift apart.';

-- The ceiling ---------------------------------------------------------------
--
-- Per-unit x N with no bound is not a limit: a forty-story feature would be
-- entitled to hold a machine for a day. `max_run_minutes` keeps its value and
-- becomes the PER-STORY allowance; this is the absolute maximum for one run.
-- NULL keeps the existing default bound, so an untouched install is bounded
-- exactly as it is now.
alter table public.runner_config
  add column if not exists max_total_run_minutes int
  check (max_total_run_minutes is null or max_total_run_minutes between 1 and 1440);

comment on column public.runner_config.max_total_run_minutes is
  'US-39.2: the most wall-clock any single run may be given, however many '
  'stories it carries. max_run_minutes is now the allowance PER STORY and is '
  'multiplied by run_work_units(); this bounds the product. NULL means the '
  '1440-minute hard bound.';

comment on column public.runner_config.max_run_minutes is
  'US-31.2, re-scoped by US-39.2: wall-clock allowance PER STORY. A run '
  'carrying N stories is claimed for max_run_minutes x N, bounded by '
  'max_total_run_minutes. A one-story run is unchanged, which is why no '
  'existing value needed migrating.';
