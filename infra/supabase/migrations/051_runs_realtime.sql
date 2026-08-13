-- 051_runs_realtime: add public.runs to the Realtime publication (US-3.21).
--
-- The PRD panel's live queued/in-progress indicator subscribes to
-- postgres_changes on public.runs (mirroring stage-tracker.tsx's
-- subscription on public.issues, already in this publication since
-- 031_issues.sql). Without this, Postgres never broadcasts runs changes
-- over Realtime and the client-side subscription silently receives nothing.

alter table public.runs replica identity full;
alter publication supabase_realtime add table public.runs;
