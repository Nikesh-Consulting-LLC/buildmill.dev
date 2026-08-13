-- 076_guidelines_ready_release: mark-guidelines-ready flag + a default
-- "Versioning & Release" guidelines section (US-7.4).
--
-- The ready flag is project-level (null = not ready), sticky (editing does not
-- auto-revoke — the UI shows an "edited since ready" nudge instead). The
-- release section is an ordinary guideline section, so it rides every surface
-- guidelines already reach with no special-casing. Canonical text lives here;
-- the seeded copy is the manager's to edit — never re-seeded after deletion.

alter table public.projects
  add column guidelines_ready_at timestamptz,
  add column guidelines_ready_by uuid;

create or replace function public.default_guidelines_release_section()
returns text
language sql
immutable
as $$
select
'How this project versions and ships. The factory computes versions for you — you never hand-pick one.

### Version scheme

Releases are versioned **`V<epic>.<release-seq>`**: the major is the current epic''s number, and the minor is a per-epic release counter that only ever increases — `V1.1`, `V1.2`, `V1.3`, then `V2.1` once Epic 1 closes and Epic 2 opens. This is intentionally not semver: the number encodes which chapter (epic) and how many releases in, not change magnitude. The factory mints and git-tags the version when you cut a release.

### Git tagging

Each cut release is tagged on the release branch head with its `V<epic>.<seq>` tag via the connected GitHub App. Tags are immutable; a promotion to Production reuses the same version rather than minting a new one.

### Changelog & release notes

A release records the work items it includes (by their epic-scoped ids, e.g. `US-1.4.1`, `BUG-1.5`). When preparing a cut, summarize what shipped — user-facing changes first, then notable fixes and internal changes — so the release notes read as a changelog for that version.

### UAT → Production promotion

Ship to UAT first, record a QA sign-off once it passes, then promote the same version to Production. Promotion never re-versions and never silently redeploys — it records the approval; you run the Production deployment when ready. The UAT and Production deployments release from the project''s configured release branches.'
$$;

create or replace function public.seed_release_guidelines_section()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.project_guidelines
    (org_id, project_id, section_key, title, content, sort_order)
  values
    (new.org_id, new.id, 'release', 'Versioning & Release',
     public.default_guidelines_release_section(), 998)
  on conflict (project_id, section_key) where section_key <> 'custom'
  do nothing;
  return new;
end;
$$;

create trigger projects_seed_release_guidelines
  after insert on public.projects
  for each row execute function public.seed_release_guidelines_section();

-- Backfill existing projects that lack the section (idempotent; first seeding).
insert into public.project_guidelines
  (org_id, project_id, section_key, title, content, sort_order)
select p.org_id, p.id, 'release', 'Versioning & Release',
       public.default_guidelines_release_section(), 998
from public.projects p
on conflict (project_id, section_key) where section_key <> 'custom'
do nothing;
