-- US-33.2: a run cannot exceed its ceiling.
--
-- A run has had no turn limit and no spend limit at all: one that goes wrong
-- goes wrong for as expensively as it likes. us-31.5 bounds how many times that
-- happens without bounding what each attempt costs.
--
-- Claude Code offers `--max-turns` and `--max-budget-usd`, but only for one
-- module and only while the CLI chooses to honour them. Authoritative
-- enforcement has to live where the factory sits — the gateway — which is why
-- this follows us-33.1's metering rather than preceding it.

-- ---------------------------------------------------------------------------
-- A sixth run status
-- ---------------------------------------------------------------------------
-- A run stopped at its budget did NOT fail — it was stopped, and it says so
-- with the number it hit. Recording it as a generic failure would feed the
-- repair loop and us-33.4's escalation a wrong premise, which is the mistake
-- us-27.12 was written about.
--
-- us-27.10 added the fifth status and left the rules for adding a sixth: every
-- live-run predicate in db.py is an ALLOW-list, so a new terminal status cannot
-- leak into one, and a test enumerates them to prove it.
alter table public.runs drop constraint if exists runs_status_check;
alter table public.runs
  add constraint runs_status_check
  check (status in ('queued', 'running', 'succeeded', 'failed', 'cancelled',
                    'stopped'));

-- Why it stopped, in the manager's language, with the number. Set by the
-- gateway at the moment of refusal — while the run is still running — so the
-- hand-back that follows can be landed as a stop rather than a failure.
alter table public.runs
  add column if not exists stopped_reason text;

comment on column public.runs.stopped_reason is
  'US-33.2: the ceiling this run hit, named with its value. Set while running; '
  'turns the following hand-back into status=stopped instead of failed.';
