-- 017_org_admin_hardening: final-review fixes for US-1.26/1.27.
--
-- 1. Revoke column-level UPDATE on organizations.is_platform_admin from
--    authenticated/anon. The "owners can update their orgs" policy (001)
--    is a broad FOR UPDATE with no WITH CHECK, and PostgREST/Supabase
--    grants column-level UPDATE on every column by default — without
--    this revoke, any org owner (i.e. everyone, since signup auto-owns
--    an org) could self-promote to platform superadmin via a direct
--    table UPDATE. Service-role and the migration-owner role bypass
--    column grants, so this does not affect the seed migration or the
--    FastAPI admin console (which uses the service-role key).
--
--    NOTE: this column-level revoke alone is NOT sufficient — see
--    018_fix_organizations_update_grant.sql, which discovered that
--    Supabase's default table-level `grant all on all tables in schema
--    public to authenticated, anon` still permits the update because
--    table-level and column-level ACL entries are independent. Kept
--    here unmodified as an accurate record of what was first tried.
revoke update (is_platform_admin) on public.organizations from authenticated, anon;

-- 2. The admin console's org list/archive endpoints were built against
--    an archived_at column that was never created.
alter table public.organizations add column archived_at timestamptz;
