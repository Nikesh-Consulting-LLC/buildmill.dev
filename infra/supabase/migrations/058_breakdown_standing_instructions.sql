-- 058_breakdown_standing_instructions: standing breakdown values on the
-- feature (US-2.28). The PRD approve dialog saves how the feature should
-- split into stories — a mode plus free-text directives for the breakdown
-- agent — and they persist for re-proposals and re-approvals until changed.
--
-- approvals gains a payload column so a gate decision can carry structured
-- audit data (here: the chosen mode + instructions at PRD approval).

alter table public.issues
  add column breakdown_mode text not null default 'automatic'
    check (breakdown_mode in ('automatic', 'single', 'multiple')),
  add column breakdown_instructions text;

alter table public.approvals
  add column payload jsonb;
