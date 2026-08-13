-- 029_archive_promote_traceability: run archive & redeploy (US-1.47),
-- promotion provenance (US-1.43), and merge-commit recording for task
-- deployment traceability (US-1.48).
--
-- Artifacts live in the private data bucket at
-- <org_id>/deployments/<deployment_id>/runs/<run_id>.<tgz|zip> — the
-- byte-exact payload the run transferred (after US-1.36 filtering),
-- api-only access, downloads stream through an org-checked endpoint.

alter table public.deployment_runs
  add column artifact_path text,
  add column artifact_bytes bigint,
  add column artifact_sha256 text,
  add column redeploy_of_run_id uuid,
  add column promoted_from_run_id uuid;

-- US-1.48: the factory records the squash-merge commit at approve time,
-- so deploy changelogs can speak in tasks instead of raw SHAs.
alter table public.runs
  add column merge_commit_sha text;
