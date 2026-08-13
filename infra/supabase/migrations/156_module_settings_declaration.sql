-- US-32.4: a module declares what it can be told, and the server remembers it.
--
-- Every tuning dial worth having is CLI-specific: `--effort`, `--fallback-model`
-- and `--append-system-prompt` are Claude Code's vocabulary, Grok spells some of
-- them differently and lacks others, OpenCode cannot take Claude's prompt shape
-- at all. A Claude-shaped settings form that three modules pretend to fit makes
-- every unsupported field a control that appears to work and silently does
-- nothing — the exact failure us-32.3 just deleted.
--
-- So each module declares its knobs beside the code that builds its command
-- line, the runner reports the declarations on `runner.hello`, and they land
-- here. Stored on the session rather than on the worker because it is a
-- statement about the bundle that machine is running, and two machines on
-- different agent versions may honestly disagree. The settings page reads the
-- most recent session, connected or not, so it can be honest about a module
-- while its machine is offline.

alter table public.runner_sessions
  add column if not exists module_settings jsonb not null default '[]'::jsonb;

comment on column public.runner_sessions.module_settings is
  'US-32.4: per-module setting declarations reported at hello — '
  '[{module, capabilities, needs_repo, settings:[{name, kind, delivery, flag, choices, help}]}]';
