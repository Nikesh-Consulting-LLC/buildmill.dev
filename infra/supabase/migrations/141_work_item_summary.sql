-- US-25.3: TLDR stores its summary instead of recomputing it.
--
-- The summary is a property of the work item, not of one popup opening, so it
-- lives on the row. `summary_source_hash` is taken over the inputs that
-- produced it: a hash rather than a timestamp comparison, for the same reason
-- us-22.7 uses one — it survives an edit that cancels out, it lets a failed
-- generation be retried without a spurious "already current", and it makes the
-- common case (open an item nobody has touched) free.
--
-- No RLS change: `issues` is already org-scoped, and these are three more
-- columns on it. Reads ride the manager's existing SELECT; the write is
-- service-role, because generation is asynchronous and must not depend on the
-- caller's JWT still being alive when the model answers.

alter table public.issues
  add column if not exists summary text,
  add column if not exists summary_generated_at timestamptz,
  add column if not exists summary_source_hash text;

comment on column public.issues.summary is
  'US-25.3: stored LLM summary of the whole work item (feature: description + approved PRD; story: story text, acceptance criteria, approved plan, instruction set). Null until first generated.';
comment on column public.issues.summary_generated_at is
  'US-25.3: when `summary` was last written. Null while no summary exists.';
comment on column public.issues.summary_source_hash is
  'US-25.3: hash over the source texts that produced `summary`. A mismatch against the current sources means the summary is stale and regenerates.';
