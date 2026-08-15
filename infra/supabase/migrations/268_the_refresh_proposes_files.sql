-- 268_the_refresh_proposes_files (us-100.5): a guidelines refresh proposes
-- whole files, and the manager decides it whole.
--
-- The refresh run and its recommendations were section-addressed: an agent
-- named a `section_key` in the twenty-two-key catalog and proposed text for
-- it, and accepting wrote `project_guidelines` — a table nothing has read
-- since migration 263 made the conventions one document. Left alone, the
-- loop would run, succeed, and change nothing an agent reads.
--
-- The rows keep their table and columns; what changes is what the columns
-- MEAN and where accept WRITES:
--
--   section_key    'agents' for the Agent Instructions document (AGENTS.md),
--                  or a run kind for that kind's `.buildmill/*.md`
--   section_title  the repo-relative path, for the review's own eyes
--   proposed_text  the file's FULL replacement text
--   section_id     null — there are no sections any more
--
-- Accepting writes `projects.agent_instructions` or upserts
-- `worker_instructions` for the kind, attributed to the deciding manager, and
-- leaves the project UNPUBLISHED (us-99.4): a refresh never puts a commit in
-- the repository on its own. A refresh is decided whole (AC1b) —
-- decide_guidelines_refresh applies or rejects every pending row in one
-- transaction — through the same per-row function the ad-hoc
-- recommend_guideline_change cards use, so there is still exactly one write
-- path.
--
-- Legacy rows (section_id set, or a key that is neither 'agents' nor a
-- kind) stay decidable: they still write project_guidelines as before, so
-- nothing already queued becomes undecidable. Production has none pending.

-- 1 ---------------------------------------------------------- scope vocabulary
-- 'all' = the document and every per-task file; 'document' = AGENTS.md only.
-- 'existing' (the old "existing sections only") stays legal for rows that
-- carry it and is read as 'document'.
alter table public.guideline_refreshes
  drop constraint if exists guideline_refreshes_scope_check;
alter table public.guideline_refreshes
  add constraint guideline_refreshes_scope_check
  check (scope in ('all', 'existing', 'document'));

comment on column public.guideline_recommendations.section_key is
  'us-100.5: the file this proposes — ''agents'' for the Agent Instructions '
  'document, or a run kind for that kind''s .buildmill file. (Pre-Phase-100 '
  'rows hold a guideline section key.)';
comment on column public.guideline_recommendations.section_title is
  'us-100.5: the repo-relative path of the proposed file, e.g. AGENTS.md or '
  '.buildmill/Code.md.';
comment on column public.guideline_recommendations.proposed_text is
  'us-100.5: the file''s FULL replacement text.';

-- 2 --------------------------------------------- one row: accept writes a file
create or replace function public.decide_guideline_recommendation(
  p_recommendation uuid,
  p_accept boolean,
  p_note text default ''
)
returns json
language plpgsql
security invoker
as $$
declare
  rec record;
  v_section uuid;
  v_target text;
begin
  select * into rec
    from public.guideline_recommendations
   where id = p_recommendation and status = 'pending'
   for update;
  if not found then
    raise exception 'recommendation not found or already decided';
  end if;

  if p_accept then
    if rec.section_key = 'agents' then
      update public.projects
         set agent_instructions = rec.proposed_text
       where id = rec.project_id;
      v_target := 'AGENTS.md';
    elsif rec.section_id is null and exists (
      select 1 from public.worker_instructions w
       where w.project_id = rec.project_id and w.run_kind = rec.section_key
    ) then
      update public.worker_instructions
         set content = rec.proposed_text,
             updated_by = auth.uid(),
             updated_at = now()
       where project_id = rec.project_id and run_kind = rec.section_key;
      v_target := rec.section_key;
    elsif rec.section_id is null and rec.section_key <> '' then
      -- A kind the project has no row for yet: seed one. The check
      -- constraint on run_kind refuses a key that is not a kind, which is
      -- the right failure for a malformed proposal.
      insert into public.worker_instructions
        (org_id, project_id, run_kind, content, updated_by)
      values
        (rec.org_id, rec.project_id, rec.section_key, rec.proposed_text, auth.uid());
      v_target := rec.section_key;
    elsif rec.section_id is not null then
      -- Legacy (pre-Phase-100) section proposal: the old write, kept so a
      -- queued row can still be closed.
      update public.project_guidelines
         set content = rec.proposed_text
       where id = rec.section_id;
      v_section := rec.section_id;
      v_target := 'legacy-section';
    else
      raise exception 'recommendation names no file (empty section_key)';
    end if;
  end if;

  update public.guideline_recommendations
     set status = case when p_accept then 'accepted' else 'rejected' end,
         decided_by = auth.uid(),
         decided_at = now(),
         decision_note = nullif(p_note, '')
   where id = p_recommendation;

  return json_build_object(
    'status', case when p_accept then 'accepted' else 'rejected' end,
    'section_id', v_section,
    'target', v_target
  );
end;
$$;

-- 3 --------------------------------------- the bundle: one decision, whole
create or replace function public.decide_guidelines_refresh(
  p_refresh uuid,
  p_accept boolean,
  p_note text default ''
)
returns json
language plpgsql
security invoker
as $$
declare
  v_ref record;
  v_rec record;
  v_applied int := 0;
  v_rejected int := 0;
begin
  select * into v_ref
    from public.guideline_refreshes
   where id = p_refresh
   for update;
  if not found then
    raise exception 'refresh not found';
  end if;
  if v_ref.status <> 'pending' then
    raise exception 'refresh already decided';
  end if;

  for v_rec in
    select id from public.guideline_recommendations
     where refresh_id = p_refresh and status = 'pending'
     order by created_at
  loop
    perform public.decide_guideline_recommendation(v_rec.id, p_accept, p_note);
    if p_accept then v_applied := v_applied + 1; else v_rejected := v_rejected + 1; end if;
  end loop;

  -- settle_guideline_refresh (173) closes the bundle when its last row
  -- leaves pending; a bundle with no rows at all needs closing here.
  update public.guideline_refreshes
     set status = 'decided', decided_at = now()
   where id = p_refresh and status = 'pending';

  return json_build_object(
    'accepted', p_accept,
    'applied', v_applied,
    'rejected', v_rejected
  );
end;
$$;

revoke all on function public.decide_guidelines_refresh(uuid, boolean, text) from public, anon;
grant execute on function public.decide_guidelines_refresh(uuid, boolean, text) to authenticated;
