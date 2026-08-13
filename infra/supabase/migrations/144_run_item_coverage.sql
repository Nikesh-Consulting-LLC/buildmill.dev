-- 144_run_item_coverage: what a multi-story run actually landed, per story
-- (US-27.1).
--
-- On 2026-07-26 run 11c564b0 built six stories, split the hand-back into
-- three tool calls because 79 files' contents do not fit in one turn, and the
-- FIRST call finalized the run: submit_changeset called perform_submit
-- unconditionally after committing. Parts 2 and 3 had nowhere to go. The
-- completion fan-out then moved all six stories to `in-review` — including
-- the two with no commit anywhere — and the run reported success.
--
-- The fix has six layers (see the story); this migration is the evidence the
-- last two stand on. Coverage is recorded per commit, and every downstream
-- status decision reads it instead of trusting what the agent said it did.
--
-- Two tables' worth of thinking in one file:
--
--   run_item_commits   append-only: one row per (commit, story it covers).
--                      A commit may cover several stories — shared scaffolding
--                      genuinely serves more than one, and forcing a single
--                      attribution would make the record lie. A story may be
--                      covered by several commits, and all of them are kept:
--                      "which commit built US-1.1.3" is asked during review,
--                      not only at hand-back.
--
--   run_items.prev_issue_status
--                      the status each story held before the run took it, so
--                      an unlanded story can be returned to the pool exactly
--                      the way runs.prev_issue_status (migration 120) returns
--                      a single-item run's issue. The stamping lives in
--                      dispatch_feature_batch and lands in migration 146 with
--                      the phase-inference fix (US-27.11) rather than
--                      re-declaring 240 lines of function twice in one phase;
--                      until then the API falls back to 'planned', which is
--                      where a buildable story sits.

alter table public.run_items
  add column if not exists prev_issue_status text;

comment on column public.run_items.prev_issue_status is
  'US-27.1: the status this story held before the run claimed it. An '
  'unlanded story is returned here rather than left in `queued`.';

-- ---------------------------------------------------------------------------
-- run_item_commits — the landed record
-- ---------------------------------------------------------------------------
create table if not exists public.run_item_commits (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  run_id uuid not null references public.runs(id) on delete cascade,
  issue_id uuid not null references public.issues(id) on delete cascade,

  commit_sha text not null,
  -- what the agent said this commit was, kept alongside the sha so the
  -- coverage line reads as prose without a GitHub round trip
  message text not null default '',
  files_changed int,

  created_at timestamptz not null default now(),

  -- one row per commit per story: a resubmitted identical sha is the same
  -- fact, not a second one
  unique (run_id, issue_id, commit_sha)
);

comment on table public.run_item_commits is
  'US-27.1: which commit landed which story''s work. The completion fan-out '
  'reads this instead of trusting the run status — an agent that claims six '
  'stories and commits four gets four stories moved.';

create index if not exists run_item_commits_run_idx
  on public.run_item_commits (run_id);
create index if not exists run_item_commits_issue_idx
  on public.run_item_commits (issue_id, created_at desc);

alter table public.run_item_commits enable row level security;

-- Read-only to clients: the review surface needs it to say "4 of 6 stories
-- landed". Writes happen in `api` against the run's own claim — a client that
-- could write one could manufacture coverage for a story with no code, which
-- is precisely the lie this table exists to make impossible.
drop policy if exists "org members read run item commits" on public.run_item_commits;
create policy "org members read run item commits"
  on public.run_item_commits for select
  using (public.is_org_member(org_id));

-- ---------------------------------------------------------------------------
-- run_coverage — the one join every surface should use
-- ---------------------------------------------------------------------------
-- Mirrors run_issue_ids (migration 138): both shapes of run answer the same
-- shape of question, so no caller has to special-case a single-story run.
-- A single-story run has no run_items row, so its coverage is whatever it
-- committed against runs.issue_id — usually nothing, because a single-story
-- run never had to name what it covered. `landed` is therefore only ever
-- consulted for multi-story runs; it is computed for both so the function
-- cannot grow a second meaning later.
create or replace function public.run_coverage(p_run uuid)
returns table (
  issue_id uuid,
  ordinal int,
  landed_sha text,
  landed_at timestamptz,
  commit_count int
)
language sql
stable
security definer
set search_path = public
as $$
  select r.issue_id,
         r.ordinal,
         c.commit_sha,
         c.created_at,
         coalesce(n.n, 0)::int
  from public.run_issue_ids(p_run) r
  left join lateral (
    select rc.commit_sha, rc.created_at
    from public.run_item_commits rc
    where rc.run_id = p_run and rc.issue_id = r.issue_id
    order by rc.created_at desc
    limit 1
  ) c on true
  left join lateral (
    select count(*) as n
    from public.run_item_commits rc
    where rc.run_id = p_run and rc.issue_id = r.issue_id
  ) n on true
  order by r.ordinal, r.issue_id
$$;

comment on function public.run_coverage(uuid) is
  'US-27.1: every issue a run covers with the last commit that landed its '
  'work (null when nothing landed). The evidence behind "4 of 6 stories '
  'landed".';

-- Live coverage in the browser: the review surface updates as each story's
-- commit lands, so a manager watching a long build sees it fill in.
do $$
begin
  alter publication supabase_realtime add table public.run_item_commits;
exception when duplicate_object then
  null;
end;
$$;
