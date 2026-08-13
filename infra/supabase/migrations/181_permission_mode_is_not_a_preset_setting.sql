-- US-47.1 — A setting that cannot change a run is not offered
--
-- `permission_mode` was a preset setting, an agent custom setting and a
-- dispatch override. It resolved through all three layers and reached the
-- runner in `runs.resolved_settings`, where `RUNNER_CLAUDE_ARGS`'s default —
-- appended AFTER the resolved settings, and Claude Code takes last-wins —
-- overrode it on every machine that had not cleared the variable.
--
-- Measured against the real CLI before deciding: under `default`, `acceptEdits`
-- and `plan`, ZERO MCP calls reach the server. A headless run has no approval
-- channel, and only `bypassPermissions` pre-approves the tools. Since every
-- code and plan run is handed --mcp-config and told to call get_work_context
-- first (us-31.9), the other three are not weaker configurations of a working
-- run — they are a run that cannot read its own work item. `plan` does it while
-- exiting 0, so a code run under it commits an empty tree and reports success.
--
-- So the setting is removed rather than repaired, and the runner states
-- `--permission-mode bypassPermissions` itself.

-- --- The stored values ------------------------------------------------------
--
-- 157 is history and is not rewritten; a later migration correcting it is the
-- pattern this repo already uses (see 164 and `max_budget_usd`). A fresh
-- database replaying both ends up correct.
update public.agent_presets
set settings = settings - 'permission_mode'
where settings ? 'permission_mode';

update public.preset_templates
set settings = settings - 'permission_mode'
where settings ? 'permission_mode';

-- Finishing 164's sweep. It stripped `max_budget_usd` from `agent_presets` and
-- not from `preset_templates`, so every org seeded since has been given a key
-- the API refuses — and a re-seed would offer it back. Same two rows, same
-- statement; the story it belongs to is us-37.2.
update public.preset_templates
set settings = settings - 'max_budget_usd'
where settings ? 'max_budget_usd';

-- `runs.resolved_settings` is deliberately NOT touched. Those rows say what
-- those runs were actually given, and rewriting them would make the record of
-- what ran under what unreadable — the same call 164 made.

-- --- The Investigate template -----------------------------------------------
--
-- Its whole premise was plan mode: "Plan-mode first... Refuses every edit". The
-- setting was inert (overridden on every box), so the preset has been doing its
-- work through high effort and its standing instructions all along. It keeps
-- those and loses the claim it cannot honour.
update public.preset_templates
set description =
      'High effort for root-cause work: read the code and the history until the '
      || 'evidence says what broke, and lead with that evidence rather than a fix.'
where key = 'investigate';

-- The org copies get the corrected description directly, and only where their
-- text is still byte-identical to the old seed — nobody wrote those words, and
-- they are now a false statement about what the preset does. An org that edited
-- the description keeps what it wrote and is offered the change by the existing
-- re-seed flow, which is the rule for anything a human touched (us-32.5).
--
-- No org's *settings* change here beyond the key removal above, and since both
-- sides lose `permission_mode` the re-seed diff for it is empty rather than an
-- offer to put it back.
update public.agent_presets p
set description = t.description
from public.preset_templates t
where p.template_key = t.key
  and t.key = 'investigate'
  and p.description = 'Plan-mode first, for root-cause work. Refuses every edit, so it can read '
      || 'anything and change nothing.';
