-- 032_issue_parent_and_touch_fixes: amends 031_issues.sql (already applied
-- live and byte-verified against the migration registry — that file is not
-- edited; both fixes below land as a new migration instead).
--
-- Finding 1 (Important): epics.updated_at had no touch trigger, unlike its
-- siblings issues_touch (031:153-155) and artifacts_touch (031:193-195).
-- It would report the creation time forever. Add the matching trigger.
--
-- Finding 2 (Important): enforce_issue_parent() (031:92-117) only guarded
-- the child side of "only a story may have a parent, and that parent must
-- be a feature". A feature with story children could be retyped away from
-- 'feature' (e.g. to 'bug') and the trigger, seeing new.parent_id is null,
-- returned early — leaving existing children parented to a non-feature
-- with no error. Add an update-path guard: reject a type change off of
-- 'feature' when the row still has children. create or replace keeps the
-- existing triggers (issues_enforce_parent) pointed at this function; no
-- trigger is dropped or recreated.

create trigger epics_touch
  before update on public.epics
  for each row execute function public.touch_updated_at();

create or replace function public.enforce_issue_parent()
returns trigger
language plpgsql
as $$
declare
  v_parent_type text;
  v_child_count bigint;
begin
  -- Guard the parent side: a feature ceasing to be a feature while it
  -- still has children would silently orphan the "story parented to a
  -- feature" invariant on every one of those children. INSERT can't
  -- already have children, so this only applies on UPDATE.
  if TG_OP = 'UPDATE' and old.type = 'feature' and new.type <> 'feature' then
    select count(*) into v_child_count from public.issues where parent_id = new.id;
    if v_child_count > 0 then
      raise exception 'cannot change type away from "feature": issue % still has % child issue(s) parented to it', new.id, v_child_count;
    end if;
  end if;

  if new.parent_id is null then
    return new;
  end if;
  if new.type <> 'story' then
    raise exception 'only a story may have a parent (this issue is a "%")', new.type;
  end if;
  if new.parent_id = new.id then
    raise exception 'an issue cannot be its own parent';
  end if;
  select type into v_parent_type from public.issues where id = new.parent_id;
  if v_parent_type is null then
    raise exception 'parent issue not found';
  end if;
  if v_parent_type <> 'feature' then
    raise exception 'a story''s parent must be a feature (parent is a "%")', v_parent_type;
  end if;
  return new;
end;
$$;
