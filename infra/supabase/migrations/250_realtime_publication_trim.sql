-- 250_realtime_publication_trim.sql
--
-- US-87.5: a subscription names its rows.
--
-- Measured on prod (pg_stat_statements, 2026-06-30 → 2026-08-12): the
-- Realtime WAL-to-JSON decoder accounted for 28,016 s of 31,286 s of total
-- database execution time — 89.5%, 2,449,775 calls at 11.4 ms — while serving
-- twelve live subscriptions. It cost 123x the next statement and 8.5x every
-- other query in the database combined.
--
-- One of the two causes is here: the publication carried 27 tables, and every
-- insert into any of them is decoded and RLS-evaluated PER SUBSCRIBER whether
-- or not a client is listening. (The other cause is unfiltered subscriptions,
-- fixed in apps/web in the same change.)
--
-- Each table below was checked against every `postgres_changes` subscription
-- in apps/web. The seven dropped here have NO subscriber anywhere:
--
--   agent_pool_placement_requests   no subscriber
--   agent_session_events            no subscriber
--   dashboard_incident_dismissals   no subscriber
--   release_prep_runs               no subscriber
--   release_test_results            no subscriber
--   run_item_commits                no subscriber
--   runner_config                   no subscriber
--
-- The twenty that remain each have one, and dropping any of them would take a
-- live surface dark with no error to show for it — which is the failure mode
-- this story is most careful about:
--
--   agent_server_jobs, agent_servers, agent_slots  servers/[id]/host-detail
--   clarifications, releases                       components/shell-live-count
--   deployment_run_events, deployment_runs         deployments/[id]/run-panel,
--                                                  activity-feed, shell-live-count
--   documents                                      components/documents-panel
--   issue_comments                                 issues/[id]/comments-panel
--   issues                                         issues hub, factory-queue,
--                                                  stage-tracker, shell-live-count
--   notifications                                  components/notification-bell
--   run_activity                                   issues/[id]/live-activity,
--                                                  runs/[id]/run-trace-live
--   run_trace                                      runs/[id]/run-trace-live
--   runner_command_audit                           team/[principalId] runner data
--   runner_incidents, runner_sessions              team-view, runner data
--   runs                                           factory-queue, prd-panel,
--                                                  run-trace-live, team-view,
--                                                  activity-feed, shell-live-count
--   suite_run_events, suite_runs                   tests/suites/[runId]
--   workspace_prep_jobs                            team/prepare-workspace-dialog
--
-- NOT DONE HERE, deliberately: five tables are SUBSCRIBED but were never in
-- the publication — issue_events, approvals, app_issues, agent_presets — so
-- those surfaces (the work item's Live activity, the Activity feed, the
-- system-issues console, the Reports hub, the agent preset list) do not
-- update live today and have not for some time. Publishing them would fix a
-- liveness bug by adding exactly the cost this story exists to remove, so it
-- is called out for its own story rather than smuggled in here.

alter publication supabase_realtime drop table public.agent_pool_placement_requests;
alter publication supabase_realtime drop table public.agent_session_events;
alter publication supabase_realtime drop table public.dashboard_incident_dismissals;
alter publication supabase_realtime drop table public.release_prep_runs;
alter publication supabase_realtime drop table public.release_test_results;
alter publication supabase_realtime drop table public.run_item_commits;
alter publication supabase_realtime drop table public.runner_config;
