-- 136: the baked plan/code instructions describe the tree that now exists (US-22.8).
--
-- After 22.2-22.5 the docs tree has an addressing scheme, a schema and a
-- machine-readable index. The baked instructions still tell agents to open
-- INDEX.md and read prose, and the code instruction still implies the tree
-- must be fetched over MCP when it is already on disk in the workspace. Two
-- surfaces describing different conventions is worse than one describing
-- none, so both are corrected here.
--
-- SURGERY, NOT A REWRITE. baked_worker_instruction has been rebuilt from an
-- older migration before (095 -> 105 -> 106) and silently lost cases each
-- time. This edits the CURRENT definition in place and RAISES if either
-- fragment is missing, so a drifted function fails loudly instead of being
-- quietly replaced by a stale one.

do $migration$
declare
  def text;
  -- Anchors start mid-literal on purpose: in the source these sentences sit
  -- inside a larger quoted chunk, not at its start.
  plan_old text := $q$If the repo carries docs/factory/INDEX.md, read the '
      || 'index and the stories that precede yours in the same feature '
      || 'before designing — the decisions your predecessors made are '
      || 'recorded there, not just implied by their code. '$q$;
  plan_new text := $q$The approved work is in the repo under docs/factory/. '
      || 'Read docs/factory/index.json for what exists and in what order, '
      || 'then the stories that precede yours in the same feature before '
      || 'designing — their approved plans and Outcome sections say what '
      || 'was decided and what actually shipped, not just what the code '
      || 'implies. '$q$;
  code_old text := $q$If the repo carries docs/factory/INDEX.md, read the '
      || 'index and the preceding stories in your feature before designing '
      || 'anything — earlier decisions live there. '$q$;
  code_new text := $q$The docs tree is already in your workspace — '
      || 'docs/factory/ is a local directory and needs no tool call. Read '
      || 'docs/factory/index.json for what exists and in what order, then '
      || 'the preceding stories in your feature before designing anything; '
      || 'their approved plans and Outcome sections carry the decisions you '
      || 'are extending. '$q$;
begin
  select pg_get_functiondef(oid) into def
  from pg_proc where proname = 'baked_worker_instruction';

  if def is null then
    raise exception 'baked_worker_instruction not found';
  end if;
  if position(plan_old in def) = 0 then
    raise exception
      'the plan run''s docs-tree sentence is not where 136 expects it — '
      'baked_worker_instruction has drifted; re-derive the patch from its '
      'current definition rather than replacing it wholesale';
  end if;
  if position(code_old in def) = 0 then
    raise exception
      'the code run''s docs-tree sentence is not where 136 expects it — '
      'baked_worker_instruction has drifted; re-derive the patch from its '
      'current definition rather than replacing it wholesale';
  end if;

  def := replace(def, plan_old, plan_new);
  def := replace(def, code_old, code_new);

  -- Belt and braces: neither old sentence may survive the edit.
  if position(plan_old in def) > 0 or position(code_old in def) > 0 then
    raise exception 'a docs-tree sentence survived the replacement';
  end if;

  execute def;
end
$migration$;
