-- 023_release_strategy: release-based deploys with instant rollback
-- (US-1.39).
--
-- deployments.strategy: 'in-place' keeps the US-1.32 behavior; 'releases'
-- lands each run in <target>/releases/<timestamp> and atomically flips a
-- <target>/current symlink on success — a failed deploy never touches the
-- live app, and rollback is a symlink flip. New deployments default to
-- 'releases'; existing rows are pinned to 'in-place' until edited.
--
-- deployment_runs.kind records rollbacks as first-class run entries
-- (who/when/from->to); release_path is nulled when retention prunes the
-- folder, so the UI knows a rollback target is gone.

alter table public.deployments
  add column strategy text not null default 'releases'
    check (strategy in ('in-place', 'releases')),
  add column keep_releases int not null default 5
    check (keep_releases between 1 and 50);

-- Existing deployments keep today's behavior until deliberately edited.
update public.deployments set strategy = 'in-place';

alter table public.deployment_runs
  add column kind text not null default 'deploy'
    check (kind in ('deploy', 'rollback')),
  add column release_path text,
  add column rollback_to_run_id uuid;
