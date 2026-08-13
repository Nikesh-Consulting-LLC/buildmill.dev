-- 123_project_task_processing: how a project routes and promotes work (US-17.1).
--
-- Two orthogonal, project-level governance knobs, both defaulting to today's
-- behaviour so existing projects change nothing:
--   * build_mode — story (freeform, as now) / feature / epic (phase-batched).
--     Routing only; the holds land in us-17.2 / us-17.3.
--   * auto_approve_{prd,plan,code} — per-gate auto-approval, off by default;
--     the promotion lands in us-17.4.
-- Distinct from dev_branch_strategy (branch/PR grouping), which is untouched.

alter table public.projects
  add column if not exists build_mode text not null default 'story'
    check (build_mode in ('story', 'feature', 'epic')),
  add column if not exists auto_approve_prd boolean not null default false,
  add column if not exists auto_approve_plan boolean not null default false,
  add column if not exists auto_approve_code boolean not null default false;

comment on column public.projects.build_mode is
  'US-17.1: story (freeform) / feature / epic. Routes work as a phase-batched '
  'unit (us-17.2/17.3); story preserves today''s freeform routing.';
comment on column public.projects.auto_approve_code is
  'US-17.1: when true, a submitted code run auto-approves — which MERGES the PR '
  '(us-17.4). Off by default. Never triggers a deploy.';
