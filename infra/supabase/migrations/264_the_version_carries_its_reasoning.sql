-- 264_the_version_carries_its_reasoning (us-100.6): an agent proposes the
-- version, and says why.
--
-- Versioning is computed today — YYYY.MM.DD.N — and CLAUDE.md states the rule
-- flatly: "The factory computes the version — you never hand-pick one
-- mid-flight." That was right when versioning was arithmetic. It stops being
-- right once the versioning rules live in the project's Agent Instructions
-- (us-100.1), because then a project has written down how it wants to be
-- versioned and the one participant that reads that document is not allowed
-- to act on it.
--
-- Purely additive: two nullable columns. `releases.version` is untouched and
-- remains the single authority once a release is cut — a proposal is an
-- input to the manager's decision, never the decision.

alter table public.releases
  add column if not exists proposed_version text,
  add column if not exists version_rationale text;

comment on column public.releases.proposed_version is
  'us-100.6: the version an agent proposed from the project''s Agent '
  'Instructions, before the manager cut. Advisory only — releases.version is '
  'what shipped. Null when no agent proposed one, which is the normal case '
  'for a project whose instructions say nothing about versioning.';

comment on column public.releases.version_rationale is
  'us-100.6: which rules the agent applied and why this is the next version. '
  'Kept so "why is this 2026.08.15.2 and not 2.1.0" has an answer six months '
  'later without reading a run trace.';
