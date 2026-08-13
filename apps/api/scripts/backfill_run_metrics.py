"""US-2.16: change-metrics backfill.

Succeeded runs from before migration 011 (change metrics) that still
carry a stored diff never had lines_added/lines_removed/files_changed/
change_breakdown computed. This one-off pass computes them from the
stored diff via the same helper the live callback uses. Runs without a
diff are left as "—".

Idempotent — only touches rows where the diff is present and
lines_added is null. Run from apps/api:
    .venv/Scripts/python.exe -m scripts.backfill_run_metrics
"""

from __future__ import annotations

import json

from app import db
from app.config import get_settings
from app.metrics import compute_diff_metrics


def backfill(settings) -> int:
    updated = 0
    with db._connect(settings) as conn:
        rows = conn.execute(
            """
            select id, diff from public.runs
            where status = 'succeeded'
              and diff is not null
              and lines_added is null
            """
        ).fetchall()
        for row in rows:
            metrics = compute_diff_metrics(row["diff"])
            if not metrics:
                continue
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
            updated += 1
        conn.commit()
    return updated


if __name__ == "__main__":
    n = backfill(get_settings())
    print(f"backfilled change metrics on {n} run(s)")
