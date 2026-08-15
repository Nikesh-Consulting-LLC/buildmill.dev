-- 260_a_failed_breakdown_gets_another_try (us-96.6): repair the features
-- the old behavior already stranded.
--
-- The code fix lives in the API (perform_submit's failure path and the
-- orphan reaper no longer move a feature to 'failed' for a breakdown-kind
-- run — the run fails, the work item stands). This migration is the data
-- half: every feature currently sitting at 'failed' with an approved PRD,
-- no live children, and a most-recent run that is a failed breakdown goes
-- back to 'ready', where the breakdown panel renders and dispatch_breakdown
-- accepts it. The repair is recorded on the item's own event feed.

with stranded as (
  select i.id, i.org_id
  from public.issues i
  where i.type = 'feature'
    and i.status = 'failed'
    and i.abandoned_at is null
    and exists (
      select 1 from public.artifacts a
      where a.issue_id = i.id and a.kind = 'prd' and a.status = 'approved'
    )
    and not exists (
      select 1 from public.issues c
      where c.parent_id = i.id and c.abandoned_at is null
    )
    and (
      select r.kind from public.runs r
      where r.issue_id = i.id
      order by r.created_at desc limit 1
    ) = 'breakdown'
    and (
      select r.status from public.runs r
      where r.issue_id = i.id
      order by r.created_at desc limit 1
    ) = 'failed'
),
fixed as (
  update public.issues i
  set status = 'ready'
  from stranded s
  where i.id = s.id
  returning i.id, i.org_id
)
insert into public.issue_events (org_id, issue_id, type, payload)
select org_id, id, 'status-repaired',
       jsonb_build_object(
         'from', 'failed', 'to', 'ready',
         'reason', 'us-96.6: a failed breakdown is the run''s failure, not the feature''s'
       )
from fixed;
