-- 224_content_audit_allow_org_detach: 223 changed content_audit.org_id to
-- ON DELETE SET NULL so deleting an org detaches its audit trail instead of
-- cascading a DELETE into the immutable trigger. Missed: a FK's SET NULL
-- action is itself an UPDATE, and content_audit_immutable() (067) blocks
-- "update or delete" both — so the org-delete cascade traded one immutable-
-- trigger rejection for another, still 'content_audit is append-only'.
--
-- Narrow the trigger: allow an UPDATE only when it does exactly what the
-- cascade does — null out org_id, touch nothing else. Any real edit to the
-- audit content (before_text, after_text, actor, action, ...) still raises.
create or replace function public.content_audit_immutable()
returns trigger
language plpgsql
as $$
begin
  if TG_OP = 'UPDATE'
     and new.org_id is null and old.org_id is not null
     and new.project_id is not distinct from old.project_id
     and new.surface is not distinct from old.surface
     and new.item_key is not distinct from old.item_key
     and new.action is not distinct from old.action
     and new.actor_type is not distinct from old.actor_type
     and new.actor_id is not distinct from old.actor_id
     and new.actor_name is not distinct from old.actor_name
     and new.before_text is not distinct from old.before_text
     and new.after_text is not distinct from old.after_text
     and new.created_at is not distinct from old.created_at
  then
    return new; -- the org-delete cascade's SET NULL, and only that.
  end if;
  raise exception 'content_audit is append-only';
end;
$$;
