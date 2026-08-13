-- 223_content_audit_survives_org_delete: an org can never be deleted while
-- it has audit history (067_content_audit.sql's immutable trigger rejects
-- update/delete unconditionally, "even the service role" — deliberately, so
-- the trail can't be quietly edited or purged). But content_audit.org_id was
-- ON DELETE CASCADE, so deleting the org itself cascades a DELETE onto its
-- audit rows and hits that same immutable trigger — meaning no org with any
-- audit history could be deleted at all, admin force-delete included.
--
-- The trail's job is to survive, not to keep its parent org alive. Detach
-- instead of cascade: org_id becomes nullable and ON DELETE SET NULL, so
-- deleting an org orphans its audit rows (history retained, no longer tied
-- to a live org) rather than either destroying them or blocking the delete.
alter table public.content_audit alter column org_id drop not null;

alter table public.content_audit
  drop constraint content_audit_org_id_fkey;

alter table public.content_audit
  add constraint content_audit_org_id_fkey
  foreign key (org_id) references public.organizations(id) on delete set null;
