-- 226_github_connection_cascades_on_user_delete: github_connections.connected_by
-- referenced auth.users(id) with no ON DELETE action (defaults to NO ACTION),
-- which blocks deleting a user outright while their org's GitHub connection
-- exists — not the intended security posture. The connection must not
-- outlive the human who authorized it (a stale, unattributed link is worse
-- than no link), but that means CASCADE, not RESTRICT: deleting the
-- connecting user removes the org's GitHub connection with them, while the
-- org itself, its projects, and everything else stays untouched —
-- github_connections has no FK relationship to projects.
alter table public.github_connections
  drop constraint github_connections_connected_by_fkey;

alter table public.github_connections
  add constraint github_connections_connected_by_fkey
  foreign key (connected_by) references auth.users(id) on delete cascade;
