-- 018_fix_organizations_update_grant: 017's column-level
-- `revoke update (is_platform_admin) ...` had no effect because
-- Supabase's default `grant all on all tables in schema public to
-- authenticated, anon` already grants table-level UPDATE, and that
-- table-level grant covers every column regardless of any column-level
-- revoke — table-level and column-level ACL entries are independent in
-- Postgres. The actual fix is to revoke the table-level UPDATE grant
-- and re-grant it scoped to only the columns owners are meant to edit
-- directly (name, archived_at). id/created_at were never meant to be
-- owner-editable either, so tightening those too is a safe side effect.
-- is_platform_admin stays excluded, closing the self-promotion path.
revoke update on public.organizations from authenticated, anon;
grant update (name, archived_at) on public.organizations to authenticated, anon;
