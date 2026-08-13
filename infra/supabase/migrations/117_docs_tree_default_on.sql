-- US-15.1 — Approved work lands on `main` by default.
--
-- US-13.4 shipped the factory docs tree (approved PRD/story/plan written into
-- the repo on approval) but left it opt-in and off by default (migration 108),
-- so on a normal project it did nothing and the manager had no reason to know
-- the switch existed. The 2026-07-23 autonomous run approved a PRD, three
-- stories and three plans and nothing reached the repo. The decision (settled
-- with the manager) is that keeping the repo — and GitHub — in sync with what
-- has been approved is the point of the factory, so it is on by default.
--
-- Two changes: flip the column default to true for new projects, and enable it
-- for existing projects (a project with sensitive requirements can still turn
-- it off; a project with no linked repo simply skips the write). The write path
-- itself (repo_docs.sync_tree) is unchanged.

alter table public.projects
  alter column docs_tree_enabled set default true;

update public.projects
  set docs_tree_enabled = true
  where docs_tree_enabled is distinct from true;
