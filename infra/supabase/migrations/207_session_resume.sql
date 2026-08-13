-- Phase 59: a run remembers its Claude session and can resume it instead of
-- restarting from nothing (us-59.1 through us-59.5, us-59.7 through us-59.9).
--
-- Claude Code's own `claude -p --resume <session-id>` is a documented,
-- headless-safe way to continue a session — full conversation history, tool
-- calls and results — from exactly where it left off. This project has never
-- captured the one thing that needs: `runs` carries no `claude_session_id`,
-- and a turn-limit hit, a crashed worker, or a clarifying question mid-task
-- all discard the session and restart the whole prompt from scratch.
--
-- ---------------------------------------------------------------------------
-- Two new statuses, distinguished from the existing five/six
-- ---------------------------------------------------------------------------
-- `paused` — the run stopped short of done for a reason that is not a
-- failure: a turn-limit hit (auto-resumable, us-59.3) or a manager-approved
-- resume of a `stopped` spend-ceiling run. Distinct from the EXISTING
-- `paused_at` column (US-15.2/118): that marks a still-`queued` run a manager
-- benched before it ever ran. This is a run that DID run and stopped
-- mid-work — a different lifecycle point entirely, so the two never collide,
-- but the names are close enough that a reader should know both exist.
--
-- `awaiting_input` — the run asked a clarifying question and parked rather
-- than polling inside a live process racing its own turn budget (us-59.5).
--
-- `abandoned` — a parked run (paused or awaiting_input) that a manager
-- deliberately closed out, or that an unattended TTL closed out on its
-- behalf (us-59.7/us-59.8). Distinct from `failed`: "we chose to stop this"
-- is a different fact than "it broke", exactly as `cancelled` (migration 145)
-- was kept distinct from `failed` for the same reason.
--
-- Every live-run predicate in db.py is an ALLOW-list keyed on `= 'queued'` or
-- `= 'running'`, never a NOT-IN over terminal statuses — so these three new
-- values cannot silently leak into a claim, a pool listing, or a reclaim
-- sweep by construction. Each caller that needs to see `paused` or
-- `awaiting_input` opts in explicitly (the new resume-claim path).
alter table public.runs drop constraint if exists runs_status_check;
alter table public.runs
  add constraint runs_status_check
  check (status in ('queued', 'running', 'succeeded', 'failed', 'cancelled',
                     'stopped', 'paused', 'awaiting_input', 'abandoned'));

-- ---------------------------------------------------------------------------
-- The session id itself (us-59.1)
-- ---------------------------------------------------------------------------
-- Captured from the CLI's `system`/`init` stream event, as soon as it is
-- observed — not deferred to a final `result` event a crash may never reach.
-- Never blanked once set: a resumed run continues the SAME session, it does
-- not mint a new one.
alter table public.runs add column if not exists claude_session_id text;

comment on column public.runs.claude_session_id is
  'us-59.1: the Claude Code CLI session id, captured from the stream''s init '
  'event as soon as it is observed. Never cleared once set — resume '
  'continues this exact session. Null means either the run predates Phase '
  '59, crashed before the CLI ever started, or is not a Claude Code run.';

-- ---------------------------------------------------------------------------
-- Why a run is parked, and how many times resume has been tried (us-59.3/59.5)
-- ---------------------------------------------------------------------------
-- Distinct from `stopped_reason` (migration 145's spend-ceiling wording,
-- gateway-stamped while still running): `resume_reason` is the runner's own
-- account of why it landed `paused` or `awaiting_input` at submit time.
alter table public.runs add column if not exists resume_reason text;
alter table public.runs add column if not exists resume_attempts integer not null default 0;
alter table public.runs add column if not exists clarification_rounds integer not null default 0;
alter table public.runs add column if not exists resume_state_at timestamptz;

comment on column public.runs.resume_reason is
  'us-59.3/59.5: why this run is paused or awaiting_input, in the runner''s '
  'own words — turn-limit hit, a clarifying question, or a manager-approved '
  'resume of a stopped run.';
comment on column public.runs.resume_attempts is
  'us-59.3: how many times auto-resume has been tried on this run''s turn-'
  'limit pause. Checked against autonomy_policy.max_resume_attempts before '
  'another auto-resume is offered; past the cap the run lands failed for '
  'real instead of pausing again.';
comment on column public.runs.clarification_rounds is
  'us-59.5: how many times request_clarification has parked this run. '
  'Checked against autonomy_policy.max_clarification_rounds — past the cap '
  'the agent is told to proceed on its own judgment rather than ask again.';
comment on column public.runs.resume_state_at is
  'us-59.3/59.5/59.7: when this run most recently entered paused or '
  'awaiting_input — the "parked for how long" clock us-59.7''s surface and '
  'us-59.8''s TTL sweep both read.';

-- ---------------------------------------------------------------------------
-- Abandon: a distinct terminal fact, manual or by timeout (us-59.7/59.8)
-- ---------------------------------------------------------------------------
-- Mirrors cancel_reason/cancelled_at/cancelled_by (migration 145) exactly,
-- for the same reason: "abandoned" must never be read as "failed", and a
-- cancelled run's own omission of finished_at applies here too — an
-- abandoned run never finished, it was let go.
alter table public.runs add column if not exists abandon_reason text;
alter table public.runs add column if not exists abandoned_at timestamptz;
alter table public.runs add column if not exists abandoned_by uuid;

comment on column public.runs.abandon_reason is
  'us-59.7/59.8: why a parked run was closed out — a manager''s own words, '
  'or "unattended past its TTL" when us-59.8''s sweep did it.';
comment on column public.runs.abandoned_at is
  'us-59.7/59.8: when a paused/awaiting_input run was abandoned. Deliberately '
  'NOT finished_at, matching cancelled_at''s reasoning (migration 145) — an '
  'abandoned run never finished, it was let go.';
comment on column public.runs.abandoned_by is
  'us-59.7: the member who abandoned this run, null when us-59.8''s TTL '
  'sweep did it instead of a person.';

-- A paused/awaiting_input run holds no active claim (its worker released the
-- lease the moment it parked, us-59.3/59.5) but keeps worker_id — the one
-- machine whose local workspace and Claude Code transcript can resume it,
-- since cross-machine resume is unproven and deliberately out of scope for
-- v1 (us-59.6 is a spike, not a build). The resume-claim path in db.py is the
-- only caller that reads status IN ('paused','awaiting_input') at all.
create index if not exists runs_resumable_idx
  on public.runs (org_id, resume_state_at)
  where status in ('paused', 'awaiting_input');

-- A `stopped` run a manager approved for resume moves through this same
-- 'paused' status and the same resume-claim path — see us-59.3's decision
-- that a spend-ceiling resume must never be silent, so it is a manager
-- action rather than automatic, but reuses the identical mechanism once
-- approved. No new status needed for that case.
