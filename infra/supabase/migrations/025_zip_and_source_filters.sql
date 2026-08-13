-- 025_zip_and_source_filters: manual zip deploys (US-1.33) and repo
-- subfolder + exclude patterns (US-1.36).
--
-- The staged zip's bytes live in the private data bucket at
-- <org_id>/deployments/<deployment_id>/staged.zip (one per deployment,
-- replaced on upload, api-only access per us-1.28 rules). Only its
-- metadata lands here so the UI can describe "Redeploy last zip".
-- Source folder / exclude patterns apply to branch payloads only —
-- a zip is a prepared artifact and ships as-is.

alter table public.deployments
  add column source_folder text not null default '',
  add column exclude_patterns text not null default '',
  add column staged_zip_filename text,
  add column staged_zip_bytes bigint,
  add column staged_zip_sha256 text,
  add column staged_zip_uploaded_by_email text,
  add column staged_zip_uploaded_at timestamptz;

alter table public.deployment_runs
  drop constraint deployment_runs_source_check;
alter table public.deployment_runs
  add constraint deployment_runs_source_check check (source in ('branch', 'zip'));
alter table public.deployment_runs
  add column zip_filename text;
