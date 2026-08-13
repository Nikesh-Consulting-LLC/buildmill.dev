-- 188_retire_prd_wireframes: US-48.6 — the app has one wireframe feature.
--
-- us-2.22 generated SVG/HTML/Mermaid wireframes inline alongside a PRD draft.
-- us-3.21 moved PRD drafting into a worker run and `_generate_prd_wireframes`
-- lost its only caller; it has been unreachable ever since. What survived was
-- worse than a deleted feature — `prd_wireframes` stayed in LLM_FUNCTIONS, so
-- Settings → LLM providers kept offering a routable function labelled
-- "PRD wireframes", described as generating wireframes alongside a PRD draft.
--
-- Somebody believed it. Prod carries one llm_function_routes row for the key,
-- pointing at Anthropic `claude-sonnet-5`, created 2026-07-26 15:05 and edited
-- again at 15:09 — attention spent twice configuring a function with no code
-- path. Since Phase 48 shipped a real `wireframe` run kind, the same entry now
-- reads as the control for THAT, which it is not.
--
-- The code half of this is deleted in the same change. This is the data half:
-- a saved route pointing at a key the registry no longer serves would render
-- as a row the Settings page cannot label.
--
-- Data only. Idempotent: a second run deletes zero rows and reports it.
-- No `llm_prompt_templates` cleanup — measured before writing this, that table
-- holds zero rows on BOTH projects, so there is no override to remove and none
-- is invented here.

do $migration$
declare
  v_deleted int;
begin
  delete from public.llm_function_routes
  where function_key = 'prd_wireframes';

  get diagnostics v_deleted = row_count;

  if v_deleted > 0 then
    raise notice
      '188: removed % llm_function_routes row(s) for the retired '
      'prd_wireframes function', v_deleted;
  else
    raise notice
      '188: no prd_wireframes routes to remove (already clean, or this '
      'project never had one)';
  end if;
end
$migration$;
