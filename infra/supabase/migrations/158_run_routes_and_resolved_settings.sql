-- US-32.6 + US-32.7: a route picks a preset, and a run records what it ran under.
--
-- us-32.6 — the agent settings page already has the right table: one row per
-- run kind, a dropdown each. Today each dropdown holds a bare model id. It
-- becomes the tuning surface: a row picks a PRESET, so "how should this agent do
-- code runs" is one choice rather than nine fields — and because no fixed set of
-- presets fits every case, any row may drop to CUSTOM and hold its own settings
-- inline. Inline rather than as an invisible preset row: minting one preset per
-- hand-tuned agent would fill the list with single-use entries and make
-- us-33.6's outcome comparison meaningless.
--
-- us-32.7 — three layers get to decide how a run executes (the agent's default,
-- the supervisor on a retry, the manager at dispatch). Three deciders and no
-- record is how a run becomes unexplainable: work is claimed from a pool, so the
-- same story handed to two agents can run two different ways and nothing would
-- say which. The resolved values AND their provenance go on the run — not a
-- preset id alone, so a run stays explainable after the preset it came from has
-- been edited.

-- ---------------------------------------------------------------------------
-- The org's default preset
-- ---------------------------------------------------------------------------
-- "Unset means inherit" needs something to inherit from. Balanced is seeded as
-- the default because it is the one written to be the ordinary case.
alter table public.agent_presets
  add column if not exists is_default boolean not null default false;

create unique index if not exists agent_presets_one_default_per_org
  on public.agent_presets (org_id) where is_default and archived_at is null;

update public.agent_presets set is_default = true
where template_key = 'balanced'
  and not exists (
    select 1 from public.agent_presets d
    where d.org_id = agent_presets.org_id and d.is_default
  );

-- ---------------------------------------------------------------------------
-- Per-kind routes on the agent
-- ---------------------------------------------------------------------------
-- Shape: { "<run kind>": {"preset_id": "<uuid>"} | {"custom": {<settings>}} }
-- An absent kind means inherit the org default preset. `model_routes` is left in
-- place: `brain` is not a preset-tunable run kind (the supervisor's own
-- reasoning routes through Settings → Routing), and it stays the model source
-- until us-32.8 delivers resolved settings to the CLI.
alter table public.runner_config
  add column if not exists run_routes jsonb not null default '{}'::jsonb;

-- Forward-migrate every configured model into an inline custom route, so no
-- existing agent quietly loses the model its manager chose.
update public.runner_config c
set run_routes = coalesce(c.run_routes, '{}'::jsonb) || (
  select coalesce(
    jsonb_object_agg(kv.key, jsonb_build_object('custom',
      jsonb_build_object('model', kv.value))),
    '{}'::jsonb)
  from jsonb_each_text(c.model_routes) kv
  where kv.key <> 'brain' and coalesce(kv.value, '') <> ''
)
where c.model_routes is not null and c.model_routes <> '{}'::jsonb;

-- ---------------------------------------------------------------------------
-- What the run actually ran under
-- ---------------------------------------------------------------------------
-- `preset_id` / `preset_version` came with 157. These carry the flattened
-- values and, per value, which layer supplied it — so a completed run reports
-- the same thing forever, whatever happens to the preset afterwards.
alter table public.runs
  add column if not exists resolved_settings jsonb,
  add column if not exists settings_sources jsonb,
  -- The preset's name as it was at resolve time: a rename must not rewrite the
  -- record of a run that already finished.
  add column if not exists preset_name text;

comment on column public.runs.resolved_settings is
  'US-32.7: the effective run settings, resolved once server-side at claim.';
comment on column public.runs.settings_sources is
  'US-32.7: per setting, the layer that supplied it — manager | supervisor | agent | org-default.';
