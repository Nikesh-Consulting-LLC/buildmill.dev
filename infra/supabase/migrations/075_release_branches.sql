-- 075_release_branches: project-level release branches + dev branching
-- strategy (US-7.3). The UAT/Production release branches are the single
-- source of truth for which branch ships to which environment; the dev
-- strategy instructs how agents branch when they write code. All three ride
-- the existing org-scoped projects RLS (client CRUD); nullable release
-- branches don't disturb existing projects.

alter table public.projects
  add column uat_branch text,
  add column production_branch text,
  add column dev_branch_strategy text not null default 'story'
    check (dev_branch_strategy in ('story', 'work_item', 'main'));

comment on column public.projects.dev_branch_strategy is
  'US-7.3: how the factory names the working branch for a coding run — '
  'story (one branch/PR per story), work_item (a feature''s stories share '
  'one branch/PR), or main (commit to the default branch, no PR).';
