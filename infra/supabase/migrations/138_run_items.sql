-- 138_run_items: a run can carry more than one work item (US-22.9).
--
-- In build_mode = 'feature' the coding phase becomes ONE run over every story
-- in the feature: one agent holding all of them, one branch, one PR, one
-- review, one merge. The stories in a feature are usually one coherent change
-- — same modules, shared types, and seams that a single agent gets right and
-- five agents in sequence get wrong.
--
-- Membership is a table, not an array on the run, so every surface can join
-- to it and a story can be traced back to the run that built it.
--
-- The alternative — keep N per-story runs pointing at one agent — would leave
-- every surface that counts, holds or traces runs quietly wrong. One run with
-- explicit membership is the smaller thing to reason about, even though it
-- touches more files.

create table if not exists public.run_items (
  run_id uuid not null references public.runs(id) on delete cascade,
  issue_id uuid not null references public.issues(id) on delete cascade,
  org_id uuid not null references public.organizations(id) on delete cascade,
  -- Build order within the run: sub_no order, so "the stories before mine"
  -- keeps meaning what it means everywhere else.
  position int not null,
  created_at timestamptz not null default now(),
  primary key (run_id, issue_id)
);

comment on table public.run_items is
  'US-22.9: the work items one run covers. A single-story run has no row here '
  '— absence means runs.issue_id is the whole membership.';

create index if not exists run_items_issue_idx on public.run_items (issue_id);
create index if not exists run_items_org_idx on public.run_items (org_id);

alter table public.run_items enable row level security;

drop policy if exists "members manage their org run items" on public.run_items;
create policy "members manage their org run items"
  on public.run_items for all
  using (public.is_org_member(org_id))
  with check (public.is_org_member(org_id));

-- Every issue a run covers, single-story runs included, so callers never have
-- to special-case the two shapes. This is the join every surface should use.
-- `ordinal`, not `position`: the latter is reserved in a RETURNS TABLE list.
-- The stored column keeps the name the story gives it.
create or replace function public.run_issue_ids(p_run uuid)
returns table (issue_id uuid, ordinal int)
language sql
stable
security definer
set search_path = public
as $$
  select ri.issue_id, ri.position
  from public.run_items ri
  where ri.run_id = p_run
  union all
  select r.issue_id, 0
  from public.runs r
  where r.id = p_run
    and not exists (select 1 from public.run_items m where m.run_id = p_run)
  order by 2, 1
$$;

comment on function public.run_issue_ids(uuid) is
  'US-22.9: the issues a run covers — its run_items membership, or just '
  'runs.issue_id when it has none. One shape for both kinds of run.';
