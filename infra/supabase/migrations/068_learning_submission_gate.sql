-- 068_learning_submission_gate: manager-gated learnings (US-5.31).
-- learning_submissions gains a decision lifecycle: submissions queue as
-- pending, the manager approves (the LLM merge runs at approval time)
-- or rejects on the project's Learnings tab. Supersedes the us-5.6
-- auto-merge. Decisions go through the API; no client write policy.

alter table public.learning_submissions
  add column status text not null default 'pending'
    check (status in ('pending', 'approved', 'rejected')),
  add column decided_by uuid,
  add column decided_at timestamptz,
  add column decision_note text;

-- Historical rows were merged the moment they arrived — auto-merge was
-- the contract when they were written. Never re-review them.
update public.learning_submissions set status = 'approved';

create index learning_submissions_pending_idx
  on public.learning_submissions (project_id, created_at)
  where status = 'pending';
