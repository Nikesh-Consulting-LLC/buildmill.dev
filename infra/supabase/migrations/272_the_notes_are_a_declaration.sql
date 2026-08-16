-- 272_the_notes_are_a_declaration (us-101.4): release notes stop being two
-- text blobs and become a document the app renders.
--
-- Why a declaration rather than the HTML page the manager first asked for —
-- two facts about this system, neither of them a matter of taste:
--
--   1. The facts a masthead needs DO NOT EXIST when the agent writes.
--      release_prep.submit fires the UAT deploy AFTER the notes are stored,
--      and suite_runs rows come later still. An agent-authored "deploy:
--      success, 1m00s · 1735 passed" is a fabrication, not a prompt problem.
--      A declaration is assembled at VIEW time, so the app fills those in
--      from data it already has.
--   2. The only safe frame for agent-authored HTML is the wireframe
--      precedent — sandbox WITHOUT allow-same-origin — and that frame has an
--      opaque origin, so a checklist inside it can never record a verdict.
--      The page would grow a second, decorative set of checkboxes beside the
--      real ones. Sign-off gates on the real ones.
--
-- Additive and nullable-by-default. notes_summary and notes_detail STAY:
-- the release list, the retry path, the document export and the
-- version-in-the-first-line rule all read them, and every release cut before
-- today has only those. A release whose notes_doc is '{}' renders exactly as
-- it does now.

alter table public.releases
  add column if not exists notes_doc jsonb not null default '{}'::jsonb;

comment on column public.releases.notes_doc is
  'us-101.4: the release notes as a declaration the app renders — standfirst, '
  'sections and prose blocks, authored whole by the release agent. The '
  'masthead facts are NOT in here: the deploy has not run when this is '
  'written, so they are rendered from deployment_runs and suite_runs at view '
  'time. Empty for every release cut before us-101.4, which renders '
  'notes_summary/notes_detail as before.';
