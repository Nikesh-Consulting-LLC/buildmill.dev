-- 055_buildmill_guidelines_section: default "Working with Build Mill"
-- guidelines section (US-5.13).
--
-- Factory-authored markdown explaining how development flows through
-- Build Mill, seeded into every project's guidelines as an ordinary
-- section — so it rides everywhere guidelines already go (dispatch
-- context, get_project_guidelines over MCP, Download, Save Instructions
-- → AGENTS.md) with zero new plumbing. The canonical text lives HERE
-- (single source); the seeded copy is the manager's to edit, reorder,
-- or delete — the factory never overwrites it and never re-seeds after
-- deletion. Content is deliberately compact: it rides into every run's
-- input_context, and it describes shipped behavior only.

create or replace function public.default_buildmill_workflow_section()
returns text
language sql
immutable
as $$
select
'This project is developed through Build Mill — an AI software factory where a human manager approves every gate and AI workers do the drafting, planning, and coding.

### Work items

Every change starts as a typed work item: a feature, bug, chore, or story. A feature first gets a PRD (problem, goals, out of scope, acceptance criteria) that the manager approves before any engineering, then splits into child stories. Bugs, chores, and standalone stories skip the PRD and go straight to planning.

### How work reaches a worker

Each item is dispatched in two phases: a plan run (write an implementation plan and a test plan — the manager approves both before any code), then a code run (implement the approved plan). Dispatched runs wait in a pool; an authorized worker — the autonomous runner or a person''s IDE agent connected over MCP — claims first-come-first-served, holds a lease, and extends it while working.

### Git and submission

Workers clone and push through the factory''s own git remote using their worker token — no GitHub credentials and no manual pull request. Work on the branch named in the run context, push it, then submit; the factory opens the PR itself.

### Context every run carries

The run context bundles the story and acceptance criteria, the governing PRD, the approved plan on code runs, these guidelines, project learnings, attached documents, and — on retries — the earlier rejection feedback. Each work item also carries a living instruction set (readable over MCP via get_instructions, no claim needed) and a comment thread shared between the manager and workers (post with add_comment).

### Review and completion

Submitted work lands in the manager''s review: approve merges the PR; send back returns it with a comment, and the retry run carries that feedback. When a feature''s last story merges, the feature completes automatically. Releases and deployments are manager-triggered, release-style with rollback.

### Ground rules for workers

- Honor the approved plan and the acceptance criteria; keep diffs focused.
- Plan runs never modify project files; code runs never open PRs or create branches beyond the named one.
- Every gate decision and state change is audited — work transparently, submit honestly.'
$$;

-- Seed new projects (after the 052 worker-instructions trigger, same event).
create or replace function public.seed_buildmill_guidelines_section()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.project_guidelines
    (org_id, project_id, section_key, title, content, sort_order)
  values
    (new.org_id, new.id, 'buildmill-workflow', 'Working with Build Mill',
     public.default_buildmill_workflow_section(), 999)
  on conflict (project_id, section_key) where section_key <> 'custom'
  do nothing;
  return new;
end;
$$;

create trigger projects_seed_buildmill_guidelines
  after insert on public.projects
  for each row execute function public.seed_buildmill_guidelines_section();

-- Backfill existing projects that lack the section (idempotent; respects
-- a manager's deletion only insofar as nothing exists yet — this is the
-- first seeding, so every project gets it once).
insert into public.project_guidelines
  (org_id, project_id, section_key, title, content, sort_order)
select p.org_id, p.id, 'buildmill-workflow', 'Working with Build Mill',
       public.default_buildmill_workflow_section(), 999
from public.projects p
on conflict (project_id, section_key) where section_key <> 'custom'
do nothing;
