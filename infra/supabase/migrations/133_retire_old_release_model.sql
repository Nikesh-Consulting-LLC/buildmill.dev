-- 133_retire_old_release_model: US-21.7.
--
-- With us-21.1–21.6 built, the old model is dead weight that still renders,
-- and two release systems on one screen is worse than either alone: a work
-- item claiming it is "in UAT" while the release that shipped it says
-- otherwise is a lie the app tells about itself.
--
-- Retired here:
--   * release_records / release_record_events — per WORK ITEM, born at merge.
--   * release_versions + cut_release_version + set_release_version_tag — the
--     V<epic>.<seq> scheme, superseded by a date-versioned release cut from
--     the default branch.
--   * the release_records write inside approve_run and auto_approve_code.
--
-- SURVIVES, and must: runs.merge_commit_sha. It is where a release resolves
-- its included work items, so dropping release_records costs nothing.
--
-- The two functions are edited from pg_get_functiondef — the database's own
-- current definition, signature and all — not retyped. Same discipline as
-- migration 131, same reason (095/105/106).

do $mig$
declare
  v_fn text;
  v_new text;
  v_name text;
begin
  foreach v_name in array array['approve_run', 'auto_approve_code'] loop
    select pg_get_functiondef(p.oid) into v_fn
    from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public' and p.proname = v_name;

    if v_fn is null then
      raise exception '% not found', v_name;
    end if;

    -- Drop the release_records upsert.
    v_new := regexp_replace(
      v_fn,
      'insert into public\.release_records.*?returning id into v_record_id;',
      ''
    );
    -- ...and the key it fed into the merged event's payload.
    v_new := regexp_replace(
      v_new,
      '''release_record_id'',\s*v_record_id,\s*',
      ''
    );

    if v_new = v_fn then
      raise exception
        'no release_records reference found in % — refusing to rewrite it blind',
        v_name;
    end if;

    execute v_new;
  end loop;
end
$mig$;

-- Prove the rewrite before dropping what it referenced: a function still
-- naming release_records would break the moment the table went.
do $check$
declare
  v_bad text;
begin
  select string_agg(p.proname, ', ') into v_bad
  from pg_proc p
  join pg_namespace n on n.oid = p.pronamespace
  where n.nspname = 'public'
    and p.proname in ('approve_run', 'auto_approve_code')
    and p.prosrc like '%release_records%';
  if v_bad is not null then
    raise exception 'still referencing release_records: %', v_bad;
  end if;
end
$check$;

-- ---------------------------------------------------------------------------
-- activity_feed's last branch reads release_record_events
-- ---------------------------------------------------------------------------
-- Dropping it with CASCADE would take the whole activity feed with it. The
-- branch is REPLACED rather than removed: releases are still real events, and
-- the new model has more of them to report than the old one did (which only
-- ever surfaced qa-signoff and promotion-approved).
--
-- The view is edited from pg_get_viewdef — the database's own definition —
-- so every other branch is carried verbatim.

do $view$
declare
  v_def text;
  v_head text;
  v_marker text := 'UNION ALL' || chr(10) || ' SELECT ''release-event:''';
begin
  v_def := pg_get_viewdef('public.activity_feed'::regclass, true);
  if position(v_marker in v_def) = 0 then
    raise exception
      'activity_feed has no release-event branch — refusing to rebuild it blind';
  end if;
  v_head := left(v_def, position(v_marker in v_def) - 1);

  execute 'create or replace view public.activity_feed as ' || v_head || $sql$
UNION ALL
 SELECT ('release:'::text || r.id) || (':'::text || ev.key) AS id,
    r.org_id,
    r.project_id,
    p.name AS project_name,
    'release'::text AS kind,
    ev.action AS action,
    'release'::text AS object_type,
    r.id AS object_id,
    r.version AS object_label,
        CASE
            WHEN ev.actor IS NOT NULL THEN 'user'::text
            ELSE 'system'::text
        END AS actor_type,
    ev.actor AS actor_id,
    ''::text AS actor_name,
        CASE
            WHEN ev.key = ANY (ARRAY['rejected'::text, 'rolled-back'::text])
            THEN 'failure'::text
            ELSE 'success'::text
        END AS outcome,
    jsonb_build_object('version', r.version, 'commit_sha', r.commit_sha) AS detail,
    ev.at AS created_at
   FROM releases r
     JOIN projects p ON p.id = r.project_id
     CROSS JOIN LATERAL ( VALUES
        ('cut'::text, 'release cut'::text, r.created_by, r.created_at),
        ('uat'::text, 'deployed to UAT'::text, NULL::uuid, r.uat_deployed_at),
        ('signoff'::text, 'UAT signed off'::text, r.signed_off_by, r.signed_off_at),
        ('promoted'::text, 'promotion approved'::text, r.promoted_by, r.promoted_at),
        ('released'::text, 'live in production'::text, NULL::uuid, r.released_at),
        ('rolled-back'::text, 'rolled back'::text, NULL::uuid, r.rolled_back_at),
        ('rejected'::text, 'release rejected'::text, NULL::uuid, r.rejected_at)
     ) AS ev(key, action, actor, at)
  WHERE ev.at IS NOT NULL$sql$;
end
$view$;

drop function if exists public.cut_release_version(uuid, text, jsonb);
drop function if exists public.set_release_version_tag(uuid, text);

drop table if exists public.release_record_events;
drop table if exists public.release_records;
drop table if exists public.release_versions;

-- `approvals.gate` keeps its check-constraint values rather than rewriting
-- history — the rows written by the old qa-signoff / promotion endpoints stay
-- readable. No new row is written for those gates; a release's own sign-off
-- and promotion records replace them.

-- ---------------------------------------------------------------------------
-- The seeded Release guideline section still taught the old scheme
-- ---------------------------------------------------------------------------
-- default_guidelines_release_section (076) describes `V<epic>.<release-seq>`
-- and release records — the model this migration just retired. It is seeded
-- into every project's guidelines and served to agents as context, so leaving
-- it would have the factory teaching a versioning rule that no longer exists.
-- Unlike baked_worker_instruction this is one self-contained block of prose,
-- not a dispatch table, so replacing it wholesale cannot drop a case.

create or replace function public.default_guidelines_release_section()
returns text
language sql
immutable
as $$
select
'How this project versions and ships. The factory computes the version — you
never hand-pick one mid-flight.

### Version scheme

A release is versioned **`YYYY.MM.DD.N`**: the date it was cut plus a
same-day counter. The manager may override the proposal when cutting, and
from that moment the version is fixed — an agent reads it off the release and
never chooses one.

### What a release is

One build, cut from the default branch and **pinned to a commit**. Everything
downstream — the notes, the UAT deployment, the promotion to production —
uses that pinned commit, never the branch head at the time it runs.

Work items are **not** linked to releases. A work item is complete when it
merges; which release carried it is a fact recorded on the release.

### The path

1. **Cut** — pins the commit, snapshots the work items merged since the last
   released version, and tags it.
2. **UAT** — an agent writes the release notes from the real commit range,
   deploys the pinned commit to the designated UAT deployment, and verifies
   its health. Every release goes to UAT first; it is not a choice.
3. **Test** — the release carries the included work items'' test cases plus
   regression cases the agent authored. A human runs them.
4. **Sign-off** — allowed only when the UAT deployment succeeded *and* every
   case passed. Blocked counts as not passed.
5. **Promote** — ships the same pinned build to production. Promotion never
   re-versions and is never automatic.

A release is **immutable**. If UAT fails, the release is rejected and a new
one supersedes it — a version name means exactly one build, forever.'
$$;
