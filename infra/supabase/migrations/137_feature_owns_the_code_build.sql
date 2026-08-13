-- 137: a story's code dispatch defers to the feature that owns it (US-22.10).
--
-- In build_mode = 'feature'/'epic' the feature owns the build. Today a story
-- at `planned` still offers Dispatch code, dispatch_issue accepts it, a run
-- is created — and us-20.5's rule (c) then holds it at claim time behind
-- whichever sibling is ahead. The manager gets a run that exists, sits in the
-- queue, and does nothing. That is worse than a refusal: there is now a
-- queued run to explain and reset.
--
-- TROUBLE IS EXEMPT, and that is the whole subtlety. us-20.5 exempts a
-- troubled story from rule (d) precisely so its fix run can still be
-- dispatched from the story page; without that, a broken story is held by its
-- own breakage and the feature deadlocks. So `failed` and `needs-fixes` keep
-- a live dispatch in every mode. Only the healthy `planned` -> code case
-- defers.
--
-- Plan dispatch is untouched: planning stays per story in feature mode.
--
-- SURGERY, NOT A REWRITE — dispatch_issue is long and has accreted rules
-- across a dozen migrations. This inserts one guard into the CURRENT
-- definition and raises if the anchor has moved.

do $migration$
declare
  def text;
  anchor text := $q$  select * into v_project from public.projects where id = v_issue.project_id;$q$;
  guard text := $q$  select * into v_project from public.projects where id = v_issue.project_id;

  -- US-22.10: in feature/epic mode the FEATURE owns the code build. Refuse
  -- here so the API and the greyed button agree — a button that declines is
  -- a better answer than a run that never moves.
  if v_kind = 'code'
     and coalesce(v_project.build_mode, 'story') in ('feature', 'epic')
     and v_issue.parent_id is not null
     and v_issue.status not in ('failed', 'needs-fixes')
  then
    declare
      v_parent_label text;
      v_sibling_count int;
    begin
      select coalesce(
               case when e.number is not null and p.item_no is not null
                 then 'FEAT-' || e.number || '.' || p.item_no
               end,
               p.title)
        into v_parent_label
      from public.issues p
      left join public.epics e on e.id = p.epic_id
      where p.id = v_issue.parent_id;

      select count(*) into v_sibling_count
      from public.issues c
      where c.parent_id = v_issue.parent_id and c.abandoned_at is null;

      raise exception
        '% owns the build — dispatch the feature to build all % stories',
        coalesce(v_parent_label, 'the feature'), v_sibling_count;
    end;
  end if;$q$;
begin
  select pg_get_functiondef(oid) into def
  from pg_proc where proname = 'dispatch_issue';

  if def is null then
    raise exception 'dispatch_issue not found';
  end if;
  if position(anchor in def) = 0 then
    raise exception
      'dispatch_issue no longer loads the project where 137 expects it — '
      're-derive this guard from the current definition rather than '
      'replacing the function wholesale';
  end if;
  if position('owns the build' in def) > 0 then
    raise exception 'the US-22.10 guard is already present';
  end if;

  def := replace(def, anchor, guard);
  execute def;
end
$migration$;
