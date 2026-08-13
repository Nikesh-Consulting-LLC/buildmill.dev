-- 079_project_summary: a long-form markdown Project Summary (US-7.8).
--
-- Distinct from the existing single-line projects.description (kept as-is for
-- the header and existing prompts). The summary seeds the AI setup brainstorm
-- and is exposed to coding agents as high-level context (us-7.15). Nullable;
-- rides the org-scoped projects RLS (client CRUD).

alter table public.projects add column summary text;

comment on column public.projects.summary is
  'US-7.8: long-form markdown description of what the project is and its goals '
  '— seeds the AI setup brainstorm and is surfaced to coding agents.';
