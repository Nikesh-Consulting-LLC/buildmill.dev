-- 177_accept_keeps_the_catalog_key: a defect found exercising US-43 on a live
-- database, after Phase 43 shipped.
--
-- `decide_guideline_recommendation` (069) creates a brand-new section with a
-- HARDCODED section_key of 'custom'. That was right when us-5.32 wrote it: a
-- recommendation could only name a section that already EXISTED (the MCP tool
-- refuses an unknown key), so the insert branch was only ever reached for a
-- genuinely new, unkeyed section.
--
-- US-43.1 changed that premise. A refresh may propose a CATALOG key the
-- project has not filled in yet — that is most of the point of the pass — and
-- US-43.4 requires the Deployment and Release section to be catalog key
-- `deployment` specifically, "a catalog section, not a custom one", so that it
-- carries guidance text, can be re-proposed against by key, and can be
-- required of the pass.
--
-- Accepting one silently downgraded it to 'custom'. Observed on dev: a
-- proposal for `tech-stack` and one for `deployment` both landed as `custom`.
-- The consequences are not cosmetic:
--   * the next refresh finds no section with that key and proposes a SECOND
--     one — the duplicate us-43.4 explicitly set out to prevent;
--   * the catalog's guidance text never renders for the section;
--   * the review page orders sections by catalog position and sorts every
--     'custom' row to the end.
--
-- The fix is one expression. Rows whose section_key is empty — every us-5.32
-- ad-hoc new-section proposal — still become 'custom', so that path is
-- unchanged.
--
-- Rebuilt from the live body, which was verified identical to 069's.

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
begin
  select * into rec
    from public.guideline_recommendations
   where id = p_recommendation and status = 'pending'
   for update;
  if not found then
    raise exception 'recommendation not found or already decided';
  end if;
  if p_accept then
    if rec.section_id is not null then
      update public.project_guidelines
         set content = rec.proposed_text
       where id = rec.section_id;
      v_section := rec.section_id;
    else
      insert into public.project_guidelines
        (org_id, project_id, section_key, title, content, sort_order)
      values
        (rec.org_id, rec.project_id,
         -- US-43: keep the catalog key the proposal named. Empty means a
         -- genuinely unkeyed section (us-5.32's path), which stays 'custom'.
         coalesce(nullif(rec.section_key, ''), 'custom'),
         coalesce(nullif(rec.section_title, ''), 'Agent-recommended section'),
         rec.proposed_text,
         coalesce((
           select max(sort_order) + 1
             from public.project_guidelines
            where project_id = rec.project_id
         ), 0))
      returning id into v_section;
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
    'section_id', v_section
  );
end;
$$;
