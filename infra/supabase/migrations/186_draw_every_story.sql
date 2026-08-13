-- 186_draw_every_story: US-48.3 — a feature draws every story it owns.
--
-- A feature is where a design decision actually lives: its stories are slices
-- of one surface, and the value of wireframing a feature is seeing its screens
-- TOGETHER, where two stories quietly proposing two different filter bars is
-- obvious. Drawing them one at a time is both tedious and the wrong shape.
--
-- The fan-out is N independent runs, never one run drawing N screens. Two
-- reasons, and the second is the load-bearing one:
--
--   * one hand-back carrying fifteen screens means one bad screen costs a
--     redraw of all fifteen; per-story runs make Redo per-story too, and
--   * US-27.1's rule — a run that claims fifteen items and delivers nine is
--     exactly the failure mode that story exists to prevent.
--
-- Deliberately NOT modelled on dispatch_feature_batch's build-mode gate. That
-- function refuses outside `feature`/`epic` mode because it batches DELIVERY
-- work, where the mode decides who owns the build. Drawing is not delivery: a
-- `story`-mode project's feature has stories with screens exactly like any
-- other, and refusing there would make the fan-out unavailable to most
-- projects for no reason anyone could act on.

create or replace function public.dispatch_wireframe_batch(p_feature uuid)
returns jsonb
language plpgsql
security invoker
set search_path = public
as $$
declare
  v_feature public.issues%rowtype;
  v_child record;
  v_run uuid;
  v_dispatched jsonb := '[]'::jsonb;
  v_skipped jsonb := '[]'::jsonb;
begin
  select * into v_feature from public.issues where id = p_feature for update;
  if not found then
    raise exception 'feature not found';
  end if;
  if v_feature.type <> 'feature' then
    raise exception
      'drawing a batch applies to a feature, not a % — draw a single story '
      'with dispatch_wireframe', v_feature.type;
  end if;
  if v_feature.abandoned_at is not null then
    raise exception 'feature is abandoned';
  end if;

  -- Every child, in sub_no order. The order matters beyond tidiness: the
  -- queue drains serially (us-20.5's one-in-flight rule, which a wireframe
  -- run stays subject to), so each run sees the screens the earlier stories
  -- declared — which is the whole reason to draw a feature as a set.
  for v_child in
    select c.id, c.title, c.abandoned_at, c.sub_no
    from public.issues c
    where c.parent_id = p_feature
    order by c.sub_no nulls last, c.created_at
  loop
    if v_child.abandoned_at is not null then
      v_skipped := v_skipped || jsonb_build_object(
        'issue_id', v_child.id, 'title', v_child.title, 'reason', 'abandoned');
      continue;
    end if;

    if exists (
      select 1 from public.runs r
      where r.issue_id = v_child.id and r.kind = 'wireframe'
        and r.status in ('queued', 'running')
    ) then
      v_skipped := v_skipped || jsonb_build_object(
        'issue_id', v_child.id, 'title', v_child.title,
        'reason', 'already-in-flight');
      continue;
    end if;

    -- Already drawn — including a `no UI surface` verdict, which IS an
    -- answer. A manager who wants that revisited does it on the story, where
    -- they can say what was wrong; a batch that silently redrew everything
    -- would re-spend the feature's whole wireframe budget on one click.
    if exists (
      select 1 from public.artifacts a
      where a.issue_id = v_child.id and a.kind = 'wireframe'
        and a.status = 'approved'
    ) then
      v_skipped := v_skipped || jsonb_build_object(
        'issue_id', v_child.id, 'title', v_child.title,
        'reason', 'already-drawn');
      continue;
    end if;

    v_run := public.dispatch_wireframe(v_child.id);
    v_dispatched := v_dispatched || jsonb_build_object(
      'issue_id', v_child.id, 'title', v_child.title, 'run_id', v_run);
  end loop;

  -- A feature whose every child is skipped is a no-op, not an error: the
  -- manager pressed a button and the honest answer is "nothing to do", which
  -- the response says in full rather than raising.
  return jsonb_build_object(
    'dispatched', v_dispatched,
    'skipped', v_skipped,
    'dispatched_count', jsonb_array_length(v_dispatched),
    'skipped_count', jsonb_array_length(v_skipped)
  );
end;
$$;

grant execute on function public.dispatch_wireframe_batch(uuid) to authenticated;

comment on function public.dispatch_wireframe_batch(uuid) is
  'US-48.3: one wireframe run per child story, in sub_no order. Abandoned, '
  'in-flight and already-drawn children are reported in `skipped` with a '
  'reason rather than failing the batch.';
