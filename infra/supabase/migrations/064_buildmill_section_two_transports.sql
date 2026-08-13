-- 064_buildmill_section_two_transports: the "Working with Build Mill"
-- factory default now describes both code hand-back transports
-- (US-5.27): MCP-only for agents (get_workspace → submit_changeset,
-- us-5.25/us-5.26) and the factory git remote for git-native workers.
--
-- Copy-at-pick semantics stand: existing copied sections keep their
-- text; new projects and new "Add section" picks get this default, and
-- a superadmin override (us-5.17) still wins over it.

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

### Getting the code and handing it back

Two transports feed one review pipeline — pick per worker:

- **Agents — MCP only (the default for AI workers):** no git tooling needed. `get_workspace` downloads the working tree as a zip pinned to a base commit; work on it locally; `submit_changeset` hands the changed files back and the factory builds the commit, pushes the work branch, and opens the PR itself. A stale base answers the current head — refetch and reapply; nothing is ever overwritten.
- **Git-native workers (humans in IDEs, or repositories above the snapshot ceiling):** clone and push through the factory''s own git remote using the worker token as the HTTP Basic password — no GitHub credentials and no manual pull request. Work on the branch named in the run context, push it, then `submit_code_work`; the factory opens the PR itself.

### Context every run carries

The run context bundles the story and acceptance criteria, the governing PRD, the approved plan on code runs, these guidelines, project learnings, attached documents, and — on retries — the earlier rejection feedback. Each work item also carries a living instruction set (readable over MCP via get_instructions, no claim needed) and a comment thread shared between the manager and workers (post with add_comment).

### Review and completion

Submitted work lands in the manager''s review: approve merges the PR; send back returns it with a comment, and the retry run carries that feedback. When a feature''s last story merges, the feature completes automatically. Releases and deployments are manager-triggered, release-style with rollback.

### Ground rules for workers

- Honor the approved plan and the acceptance criteria; keep diffs focused.
- Plan runs never modify project files; code runs never open PRs or create branches beyond the named one.
- Every gate decision and state change is audited — work transparently, submit honestly.'
$$;
