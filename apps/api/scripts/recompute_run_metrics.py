"""us-109.3: recompute change metrics that were counted before vendored
paths were excluded.

`backfill_run_metrics` only fills rows where `lines_added is null` — by
design, since it was a one-off for runs predating the columns. This one
RE-computes every succeeded run that still has its diff, because the answer
itself changed: a dependency tree, build output, lockfile or minified bundle
in a changeset no longer counts as authored lines.

It then repairs `agent_effort_daily`, which the trigger accumulates
incrementally (migration 252) and therefore cannot self-correct — the three
line/file columns are recomputed from `runs` for exactly the (org, worker,
day) keys whose runs moved. work_seconds, runs_finished, tokens and cost are
NOT touched: this change cannot affect them, and rewriting them from `runs`
would silently discard any adjustment made by hand.

Dry by default. Run from apps/api:
    .venv/Scripts/python.exe -m scripts.recompute_run_metrics          # report
    .venv/Scripts/python.exe -m scripts.recompute_run_metrics --apply  # write
"""

from __future__ import annotations

import argparse
import json

from app import db
from app.config import get_settings
from app.metrics import compute_diff_metrics


def recompute(settings, apply: bool) -> dict:
    changed: list[dict] = []
    with db._connect(settings) as conn:
        rows = conn.execute(
            """
            select id, org_id, worker_id, diff,
                   lines_added, lines_removed, files_changed,
                   (coalesce(finished_at, claimed_at) at time zone 'utc')::date as day
              from public.runs
             where status = 'succeeded'
               and diff is not null
               and lines_added is not null
            """
        ).fetchall()

        for row in rows:
            metrics = compute_diff_metrics(row["diff"])
            if not metrics:
                continue
            if (
                metrics["lines_added"] == row["lines_added"]
                and metrics["lines_removed"] == row["lines_removed"]
                and metrics["files_changed"] == row["files_changed"]
            ):
                continue
            changed.append(
                {
                    "run_id": str(row["id"]),
                    "org_id": str(row["org_id"]) if row["org_id"] else None,
                    "worker_id": str(row["worker_id"]) if row["worker_id"] else None,
                    "day": str(row["day"]) if row["day"] else None,
                    "was": [
                        row["lines_added"],
                        row["lines_removed"],
                        row["files_changed"],
                    ],
                    "now": [
                        metrics["lines_added"],
                        metrics["lines_removed"],
                        metrics["files_changed"],
                    ],
                }
            )
            if apply:
                conn.execute(
                    """
                    update public.runs
                       set lines_added = %s, lines_removed = %s,
                           files_changed = %s, change_breakdown = %s
                     where id = %s
                    """,
                    (
                        metrics["lines_added"],
                        metrics["lines_removed"],
                        metrics["files_changed"],
                        json.dumps(metrics["change_breakdown"]),
                        row["id"],
                    ),
                )

        # The rollup is an accumulator, so only a recompute from `runs` can
        # correct it. Scoped to the affected keys — every other day's row is
        # correct and must not be rewritten.
        keys = {
            (c["org_id"], c["worker_id"], c["day"])
            for c in changed
            if c["org_id"] and c["worker_id"] and c["day"]
        }
        if apply:
            for org_id, worker_id, day in keys:
                conn.execute(
                    """
                    update public.agent_effort_daily a
                       set lines_added = coalesce(t.added, 0),
                           lines_removed = coalesce(t.removed, 0),
                           files_changed = coalesce(t.files, 0),
                           updated_at = now()
                      from (
                        select sum(coalesce(r.lines_added, 0))   as added,
                               sum(coalesce(r.lines_removed, 0)) as removed,
                               sum(coalesce(r.files_changed, 0)) as files
                          from public.runs r
                         where r.org_id = %s
                           and r.worker_id = %s
                           and (coalesce(r.finished_at, r.claimed_at)
                                at time zone 'utc')::date = %s
                           and r.status in ('succeeded', 'failed', 'cancelled',
                                            'abandoned', 'stopped')
                      ) t
                     where a.org_id = %s and a.worker_id = %s and a.day = %s
                    """,
                    (org_id, worker_id, day, org_id, worker_id, day),
                )
            conn.commit()

    return {"runs": changed, "rollup_keys": len(keys)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write the changes")
    args = ap.parse_args()

    result = recompute(get_settings(), args.apply)
    verb = "updated" if args.apply else "would update"
    for c in sorted(result["runs"], key=lambda c: -(c["was"][0] or 0))[:20]:
        print(
            f"  {c['run_id']}  {c['day']}  "
            f"+{c['was'][0]} -> +{c['now'][0]} lines, "
            f"{c['was'][2]} -> {c['now'][2]} files"
        )
    print(
        f"{verb} {len(result['runs'])} run(s) and "
        f"{result['rollup_keys']} agent_effort_daily row(s)"
    )
    if not args.apply:
        print("dry run — pass --apply to write")
