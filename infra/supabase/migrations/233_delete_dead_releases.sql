-- 233_delete_dead_releases: us-70.1.
--
-- Releases accumulated forever: rejected, failed and cancelled cuts sat in
-- the hub with no way to remove them (28 dead records against one released
-- build in prod). Deletion becomes a database right with both gates in the
-- policy itself: only an owner or admin, and only a release whose status is
-- terminal-unsuccessful. A released, promoting, or in-UAT record stays
-- undeletable by construction — widening that takes another migration, not
-- a UI change. Children (release_prep_runs, release_test_results) already
-- cascade on their foreign keys; deployment_runs.release_id keeps its row
-- (the deployment happened — deleting the bookkeeping must not rewrite the
-- machine's history). Versions are never reused, so deleting a record does
-- not free its name.

create policy "owner or admin deletes dead releases" on public.releases
  for delete using (
    status in ('rejected', 'failed', 'cancelled')
    and exists (
      select 1
      from public.organization_members m
      join public.principals pr on pr.id = m.principal_id
      where m.org_id = releases.org_id
        and pr.auth_user_id = (select auth.uid())
        and m.status = 'active'
        and m.role in ('owner', 'admin')
    )
  );
