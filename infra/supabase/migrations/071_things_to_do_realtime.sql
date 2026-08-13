-- 071_things_to_do_realtime: publish the last two tables the Things to Do
-- decision hub (US-6.1) subscribes to for live updates.
--
-- The page already reacts to issues (031), runs (051), and deployment_runs
-- (021) via the Realtime publication. Worker questions and release/deploy
-- events also drive the "waiting on you" count, so add their tables too.
-- Guarded so re-running (or landing out of order next to a concurrent
-- migration) is a no-op rather than a duplicate-table error.

do $$
begin
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime'
      and schemaname = 'public'
      and tablename = 'clarifications'
  ) then
    alter publication supabase_realtime add table public.clarifications;
  end if;

  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime'
      and schemaname = 'public'
      and tablename = 'release_record_events'
  ) then
    alter publication supabase_realtime add table public.release_record_events;
  end if;
end $$;
