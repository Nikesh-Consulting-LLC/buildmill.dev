"""Direct Postgres access for runner orchestration (US-1.10, US-2.1).

The runner is a trusted machine process authenticated by shared secret,
not a Supabase user — its claim/callback writes go straight to Postgres.
User-facing endpoints never use this; they go through PostgREST + RLS.
"""

import contextvars
import datetime as dt
import hashlib
import json
import logging
import re
import secrets
import threading
import time
import uuid as _uuid_mod
from typing import Any

import psycopg

from .config import Settings
from .metrics import compute_diff_metrics
from .pool import pool_for

logger = logging.getLogger(__name__)

# US-62.8: how much of the current request's time was spent inside a `with
# _connect(...)` block, across every call `db.py` makes during it — a single
# accumulator, since one request can open several connections in sequence.
# `None` outside a request the timing middleware wraps (e.g. a background
# sweep), in which case `_TimedConnection` below is a no-op.
_request_db_ms: contextvars.ContextVar[list[float] | None] = contextvars.ContextVar(
    "request_db_ms", default=None
)


def begin_request_timing() -> contextvars.Token:
    """Called once by the request-timing middleware, per request."""
    return _request_db_ms.set([0.0])


def end_request_timing(token: contextvars.Token) -> int:
    """The accumulated DB time for this request, in ms, and resets the
    contextvar so the next request on this thread starts from zero."""
    acc = _request_db_ms.get()
    ms = round(acc[0]) if acc else 0
    _request_db_ms.reset(token)
    return ms


class _TimedConnection:
    """Leases a connection from the process pool for the duration of a `with`
    block, and adds the wall-clock time spent inside it to the current
    request's DB-time accumulator — every other attribute (`.execute`,
    `.commit`, ...) reaches the real connection completely unchanged. A no-op
    outside `begin_request_timing`'s scope (a background sweep, a test's fake
    settings), since there is nothing to accumulate into.

    US-87.6: this used to wrap a freshly-opened connection that its `with`
    block then closed. It now wraps the pool's own context manager, which
    commits on success, rolls back on an exception, and RETURNS the
    connection instead of closing it. The 214 `with _connect(settings) as
    conn:` call sites are unchanged, and so is what they observe: the same
    dict rows, the same 15 s statement timeout, the same explicit
    `conn.commit()` where they already called it."""

    def __init__(self, pool):
        self._pool = pool
        self._cm = None
        self._start: float | None = None

    def __enter__(self):
        self._start = time.monotonic()
        self._cm = self._pool.connection()
        return self._cm.__enter__()

    def __exit__(self, exc_type, exc, tb):
        try:
            return self._cm.__exit__(exc_type, exc, tb)
        finally:
            if self._start is not None:
                elapsed_ms = (time.monotonic() - self._start) * 1000
                acc = _request_db_ms.get()
                if acc is not None:
                    acc[0] += elapsed_ms


def coerce_acceptance_criteria(value: Any) -> list[str]:
    """US-15.8: acceptance_criteria must land in the jsonb column as an array
    of strings. A breakdown worker that hands its criteria back as one
    numbered block of text (``"1. …\\n2. …"``) instead of a JSON array would
    otherwise be stored as a jsonb *string*, and the story page crashes when it
    calls ``.map`` on it. Defense in depth behind validate_stories: a list
    keeps its non-empty string items; a string is split on newlines with any
    leading list marker (``1.``/``2)``/``-``/``*``/``•``) stripped; anything
    else becomes an empty list."""
    if isinstance(value, list):
        return [str(c).strip() for c in value if str(c).strip()]
    if isinstance(value, str):
        out: list[str] = []
        for line in value.splitlines():
            cleaned = re.sub(r"^\s*(?:\d+[.)]|[-*•])\s*", "", line).strip()
            if cleaned:
                out.append(cleaned)
        return out
    return []


def _connect(settings: Settings):
    """US-87.6: a leased connection, not a new one. See app/pool.py."""
    return _TimedConnection(pool_for(settings))


# US-87.6: request rows are buffered and written in batches.
#
# `record_api_request` used to open its OWN connection for a single insert,
# 584,613 times over six weeks on prod. It was correctly off the response path
# (`asyncio.create_task` + `to_thread` in main.py), so it cost no latency —
# but it doubled the process's connection count and took a threadpool slot per
# request, which under load starves every other synchronous database call.
#
# Losing the tail of this buffer when the process exits is acceptable: this is
# a diagnostic table (US-62.8), and dropping a few rows from it is strictly
# better than making a request wait on writing one.
_LOG_BUFFER: list[tuple[str, str, int, int, int]] = []
_LOG_BUFFER_LOCK = threading.Lock()
_LOG_BATCH_SIZE = 50


def record_api_request(
    settings: Settings,
    route: str,
    method: str,
    status_code: int,
    duration_ms: int,
    db_ms: int,
) -> None:
    """US-62.8: one row per API request — total duration and how much of it
    was spent in the database, so a slow endpoint is diagnosable as "the
    query" versus "everything else" without a profiler. Called fire-and-
    forget from the request-timing middleware; a logging failure must never
    be visible to the caller, whose response has already been sent."""
    row = (route[:200], method[:10], status_code, duration_ms, db_ms)
    with _LOG_BUFFER_LOCK:
        _LOG_BUFFER.append(row)
        if len(_LOG_BUFFER) < _LOG_BATCH_SIZE:
            return
        batch = _LOG_BUFFER[:]
        _LOG_BUFFER.clear()
    _flush_api_request_batch(settings, batch)


def _flush_api_request_batch(
    settings: Settings, batch: list[tuple[str, str, int, int, int]]
) -> None:
    if not batch:
        return
    try:
        with _connect(settings) as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    insert into public.api_request_log
                      (route, method, status_code, duration_ms, db_ms)
                    values (%s, %s, %s, %s, %s)
                    """,
                    batch,
                )
            conn.commit()
    except Exception:  # noqa: BLE001 — logging must never break a request
        logger.debug("api_request_log write failed", exc_info=True)


def flush_api_request_log(settings: Settings) -> None:
    """Drain whatever is buffered. Called from the API's lifespan shutdown so
    a clean stop keeps its tail; a hard kill is allowed to lose it."""
    with _LOG_BUFFER_LOCK:
        batch = _LOG_BUFFER[:]
        _LOG_BUFFER.clear()
    _flush_api_request_batch(settings, batch)


# US-27.1: a multi-story run's membership, with the count of commits that
# have actually landed each story's work. Everything downstream of a
# hand-back reads this rather than the agent's account of what it did.
def _run_member_rows(conn, run_id: str) -> list[dict[str, Any]]:
    return conn.execute(
        """
        select ri.issue_id, ri.position, ri.prev_issue_status,
               (select count(*) from public.run_item_commits c
                 where c.run_id = ri.run_id and c.issue_id = ri.issue_id) as commits
        from public.run_items ri
        where ri.run_id = %s
        order by ri.position
        """,
        (run_id,),
    ).fetchall()


def _fan_out_issue_status(conn, run: dict[str, Any], issue_status: str) -> None:
    """US-27.1 layer 4: move the stories a run covered — and only the ones it
    has a commit for.

    Run 11c564b0 moved six stories to `in-review` while holding a commit for
    four of them, because the fan-out asked the run's status what happened
    instead of asking the record. This is the layer that would have caught
    that with every other layer removed: it does not trust the agent at all.

    A success moves the landed stories forward and returns the unlanded ones
    to where they were, as work still to do. A failure is shared — the run
    failed as a whole, and a story whose commit landed inside a failed run is
    not finished. Single-story runs (no run_items) keep today's behaviour
    byte for byte."""
    members = _run_member_rows(conn, str(run["id"]))
    if not members:
        conn.execute(
            "update public.issues set status = %s "
            "where id in (select issue_id from public.run_issue_ids(%s))",
            (issue_status, run["id"]),
        )
        return

    landed = [m for m in members if m["commits"]]
    unlanded = [m for m in members if not m["commits"]]

    if issue_status in ("in-review", "merged"):
        if landed:
            conn.execute(
                "update public.issues set status = %s where id = any(%s)",
                (issue_status, [m["issue_id"] for m in landed]),
            )
        for m in unlanded:
            target = m["prev_issue_status"] or _RESET_FALLBACK_STATUS["code"]
            conn.execute(
                "update public.issues set status = %s where id = %s",
                (target, m["issue_id"]),
            )
            conn.execute(
                """
                insert into public.issue_events (org_id, issue_id, type, payload)
                values (%s, %s, 'returned-to-pool', %s)
                """,
                (
                    run["org_id"],
                    m["issue_id"],
                    json.dumps(
                        {
                            "run_id": str(run["id"]),
                            "kind": run.get("kind") or "code",
                            "returned_to_status": target,
                            "reason": (
                                "the run finished without a commit covering "
                                "this story"
                            ),
                        }
                    ),
                ),
            )
    else:
        conn.execute(
            "update public.issues set status = %s where id = any(%s)",
            (issue_status, [m["issue_id"] for m in members]),
        )

    # US-27.1: the run's own issue moves with its members. Today the six
    # stories reach `in-review` and the feature they belong to stays `queued`,
    # so the board shows a feature still waiting on the factory with every
    # child already past it.
    member_ids = {str(m["issue_id"]) for m in members}
    if run.get("issue_id") and str(run["issue_id"]) not in member_ids:
        conn.execute(
            "update public.issues set status = %s where id = %s",
            (issue_status, run["issue_id"]),
        )


def _next_artifact_version(conn, issue_id: str, kind: str) -> int:
    row = conn.execute(
        """
        select coalesce(max(version), 0) + 1 as v
        from public.artifacts
        where issue_id = %s and kind = %s
        """,
        (issue_id, kind),
    ).fetchone()
    return int(row["v"])


def complete_run(
    settings: Settings,
    run_id: str,
    outcome: str,
    stdout: str | None,
    diff: str | None,
    branch_ref: str | None,
    pr_url: str | None,
    error: str | None,
    test_cases: list[dict[str, Any]] | None = None,
    plan: str | None = None,
    test_plan: str | None = None,
    prd: str | None = None,
    stories: list[dict[str, Any]] | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    cost_usd: float | None = None,
    worker_name: str | None = None,
    trigger: str | None = None,
    direct: bool = False,
    claude_session_id: str | None = None,
) -> bool:
    """Record a runner callback. Returns False if the run isn't running.
    US-7.15: `direct` marks a main-strategy code landing (committed to the
    default branch, no PR) — the review gate is bypassed and the item is
    merged directly.
    US-59.1: `claude_session_id` rides every callback, success or not, and is
    written with coalesce so it is never blanked once captured."""
    # us-96.11: runs.stdout is rendered by the dashboard like a trace —
    # header-shaped credentials are masked at write time, same as
    # record_run_trace.
    stdout = scrub_credential_patterns(stdout)
    error = scrub_credential_patterns(error)
    metrics = compute_diff_metrics(diff) if outcome == "succeeded" else None

    # US-31.1: Postgres text refuses NUL; one 0x00 in a CLI's output must
    # never turn a run report into an exception. The API boundary strips
    # these too — this covers direct db callers.
    stdout = stdout.replace("\x00", "") if stdout else stdout
    error = error.replace("\x00", "") if error else error
    diff = diff.replace("\x00", "") if diff else diff

    with _connect(settings) as conn:
        run = conn.execute(
            """
            update public.runs
            set status = %s, stdout = %s, diff = %s, branch_ref = %s,
                pr_url = %s, error = %s, finished_at = now(),
                lines_added = %s, lines_removed = %s, files_changed = %s,
                change_breakdown = %s,
                tokens_in = %s, tokens_out = %s, cost_usd = %s,
                claude_session_id = coalesce(%s, claude_session_id)
            where id = %s and status = 'running'
            -- US-31.5: worker_id comes back so a failed run's attempt is
            -- attributed to the agent that made it (by id, never by name).
            returning id, org_id, issue_id, kind, input_context, worker_id
            """,
            (
                outcome,
                stdout,
                diff,
                branch_ref,
                pr_url,
                error,
                metrics["lines_added"] if metrics else None,
                metrics["lines_removed"] if metrics else None,
                metrics["files_changed"] if metrics else None,
                json.dumps(metrics["change_breakdown"]) if metrics else None,
                tokens_in,
                tokens_out,
                cost_usd,
                claude_session_id,
                run_id,
            ),
        ).fetchone()
        if not run:
            return False

        kind = run.get("kind") or "code"
        if outcome == "failed":
            # US-31.5: a failed run consumes an attempt. `cancelled` never
            # reaches here (cancel_run has its own path) and deliberately
            # consumes nothing — the manager withdrew it.
            # us-96.9: `stopped` has a producer again — the manager's own
            # session stop (set_run_stopped_reason + the submit route's
            # US-33.2 mapping) — and it consumes nothing and writes NO
            # agent-failure row: a decision is not a malfunction, and on
            # 2026-08-14 one stop produced two spurious failure records and
            # a 42-minute "failure" in the effort rollup.
            record_run_attempt(
                conn,
                str(run["org_id"]),
                str(run["issue_id"]) if run["issue_id"] else None,
                run_id,
                str(run["worker_id"]) if run.get("worker_id") else None,
                kind,
                outcome,
            )
            # US-79.8: the failure also lands in the superadmin's Agent
            # failures console, with the error the agent reported and the
            # tail of what it printed.
            record_agent_failure(
                conn,
                run_id,
                "run-failed",
                worker_id=str(run["worker_id"]) if run.get("worker_id") else None,
                error=error,
                detail={
                    "outcome": outcome,
                    **({"stdout_tail": stdout[-2000:]} if stdout else {}),
                },
            )
        if kind == "release" and outcome != "succeeded":
            # Symmetric to claim_run's queued->running write: nothing else
            # ever moves releases.status off 'running' on failure, so a
            # failed release run left the release permanently "in flight"
            # per releases_one_in_flight_per_project (migration 130) -
            # blocking both a new cut and the cancel button (which only
            # accepts 'queued'). Scoped to the same in-flight status set the
            # partial unique index uses, so an already-terminal release is
            # never clobbered by a stray/late callback.
            release_id = (run.get("input_context") or {}).get("release_id")
            if release_id:
                conn.execute(
                    """
                    update public.releases
                    set status = 'failed',
                        failure_reason = left(coalesce(%s, 'release run ' || %s), 500),
                        updated_at = now()
                    where id = %s
                      and status in ('queued', 'running', 'uat-deployed',
                                      'uat-signed-off', 'promoting')
                    """,
                    (error, outcome, release_id),
                )
        if outcome != "succeeded":
            # A failed prd run must not strand the issue in 'failed': that
            # status blocks re-dispatching a prd run (dispatch_prd_draft only
            # accepts draft/prd-review/ready) and hides the frontend's Draft
            # PRD button. claim_run never mutates issue status for prd runs
            # either, so leave it as-is here and only record the event.
            # US-13.11: a failed test run likewise leaves the issue where it
            # sits (in-review) — verification failing is not the work failing.
            # US-44.1: an elaborate run joins prd and test here. A failed
            # proposal is not a failed story — the text it was going to
            # rewrite is exactly as good as it was before the run started,
            # and marking the item `failed` would block the dispatch paths
            # that only accept draft/ready.
            # us-96.6: breakdown joins them. A failed split is the RUN's
            # failure, not the feature's: 'failed' stranded the feature
            # outright (dispatch_breakdown only accepts 'ready', the
            # stories panel only renders there, and dispatch_issue would
            # re-plan — the wrong kind). The feature stays 'ready' and the
            # manager just dispatches the breakdown again.
            issue_status = (
                None if kind in ("prd", "test", "elaborate", "breakdown") else "failed"
            )
            # us-96.9: a manager stop returns the item to where it stood at
            # dispatch (prev_issue_status, migration 120) so re-dispatching
            # is one step — landing it 'failed' would colour a decision as
            # a defect everywhere failure data is read.
            if outcome == "stopped" and issue_status is not None:
                prev = conn.execute(
                    "select prev_issue_status from public.runs where id = %s",
                    (run_id,),
                ).fetchone()
                issue_status = (prev or {}).get("prev_issue_status") or issue_status
            # US-33.2: a distinct event, because a stop is a distinct outcome —
            # the feed should not read as if the work failed.
            event = "run-stopped" if outcome == "stopped" else "run-failed"
        elif kind == "test":
            # US-13.11: verification changes no issue state — the
            # deliverable is per-case results, already recorded via
            # report_test_results on the same review surface.
            issue_status = None
            event = "test-run-completed"
        elif kind == "release":
            # US-13.12: a completed release cut changes no issue and no
            # environment — its deliverables (notes document, optional
            # promotion PR) were attached at submit time.
            issue_status = None
            event = "release-run-completed"
        elif kind == "elaborate":
            # US-44.1: the story's status never moves for an elaboration —
            # the proposal is an artifact awaiting the manager, and the item
            # is exactly where it was. Approving the proposal is what may
            # move it (draft → ready), and that happens at the gate.
            issue_status = None
            event = "elaboration-ready"
        elif kind == "guidelines":
            # US-43.6: a project-scoped run — there is no issue to move, and
            # that is the point. us-43.1 set `in-review` here, which is a
            # WAITING status, so the refresh appeared in the Reviews group
            # beside real code reviews and led to a gate offering
            # approve/reject over a pull request that never existed.
            issue_status = None
            event = "guidelines-refresh-ready"
        elif kind == "wireframe":
            # US-54.3: the drawing has no approval gate (US-48.2) — it is
            # live on landing and the manager's lever is Redo, so the story
            # is not this run's to move. Falling through to `in-review`
            # stamped a WAITING status onto a story whose code run was two
            # minutes into building it, and the review page dressed the
            # merge gate over a run with no diff and no PR.
            issue_status = None
            event = "wireframe-ready"
        elif kind == "plan":
            issue_status = "plan-review"
            event = "plan-ready"
        elif kind == "prd":
            issue_status = "prd-review"
            event = "prd-drafted"
        elif kind == "breakdown":
            # US-2.33: success creates child stories (below); the feature
            # stays a ready container.
            issue_status = "ready"
            event = "stories-created"
        elif direct:
            # US-7.15: main-strategy landing — committed to the default
            # branch, no PR, review gate bypassed; the item is merged.
            issue_status = "merged"
            event = "merged"
        else:
            issue_status = "in-review"
            event = "run-succeeded"

        if issue_status:
            # US-22.9 / US-27.1: hand-back moves the stories the run covered —
            # a success moves the ones it has a commit for and returns the
            # rest to the pool; a failure is shared by all of them.
            _fan_out_issue_status(conn, run, issue_status)

        story_ids: list[str] = []
        if outcome == "succeeded" and kind == "plan":
            # Supersede prior draft/approved plan artifacts; store new versions.
            conn.execute(
                """
                update public.artifacts
                set status = 'superseded'
                where issue_id = %s
                  and kind in ('plan', 'test_plan')
                  and status in ('draft', 'approved')
                """,
                (run["issue_id"],),
            )
            plan_body = plan or (stdout or "# Plan\n\n(empty agent plan)")
            test_body = test_plan or "# Test plan\n\n(empty agent test plan)"
            for art_kind, content in (("plan", plan_body), ("test_plan", test_body)):
                version = _next_artifact_version(conn, str(run["issue_id"]), art_kind)
                conn.execute(
                    # US-49.2: stamp the brief this was written from. The
                    # item's set is living and US-49.1 makes editing it easy,
                    # so reading it later answers a different question.
                    """
                    insert into public.artifacts
                      (org_id, issue_id, kind, content, version, status, created_by,
                       instruction_set)
                    values (%s, %s, %s, %s, %s, 'draft', 'agent',
                            (select instruction_set from public.issues where id = %s))
                    """,
                    (
                        run["org_id"],
                        run["issue_id"],
                        art_kind,
                        content,
                        version,
                        run["issue_id"],
                    ),
                )
        elif outcome == "succeeded" and kind == "prd":
            # Supersede prior draft/approved PRD artifacts; store a new version
            # (mirrors the old synchronous draft_prd endpoint, US-2.3).
            conn.execute(
                """
                update public.artifacts
                set status = 'superseded'
                where issue_id = %s and kind = 'prd' and status in ('draft', 'approved')
                """,
                (run["issue_id"],),
            )
            version = _next_artifact_version(conn, str(run["issue_id"]), "prd")
            content = prd or (stdout or "# PRD\n\n(empty agent draft)")
            conn.execute(
                # US-49.2: see the plan insert above — the PRD keeps the
                # instructions it was written from.
                """
                insert into public.artifacts
                  (org_id, issue_id, kind, content, version, status, created_by,
                   instruction_set)
                values (%s, %s, 'prd', %s, %s, 'draft', 'agent',
                        (select instruction_set from public.issues where id = %s))
                """,
                (
                    run["org_id"],
                    run["issue_id"],
                    content.strip(),
                    version,
                    run["issue_id"],
                ),
            )
        elif outcome == "succeeded" and kind == "breakdown":
            # US-2.33: auto-create each proposed slice as a draft child story,
            # inheriting the feature's org/project/epic. 'single' mode is
            # enforced deterministically (the prompt asks for one; trust but
            # verify). Complexity scoring runs off the critical path in
            # perform_submit against the new children.
            ic = run.get("input_context") or {}
            items = list(stories or [])
            if (ic.get("breakdown_mode") or "automatic") == "single" and len(items) > 1:
                items = items[:1]
            for s in items:
                title = (str(s.get("title") or "").strip()) or "Untitled story"
                body = (str(s.get("body") or "").strip()) or None
                # US-15.8: never store a bare string — always a jsonb array.
                ac = coerce_acceptance_criteria(s.get("acceptance_criteria"))
                child = conn.execute(
                    """
                    insert into public.issues
                      (org_id, project_id, type, parent_id, epic_id, title, body,
                       acceptance_criteria, status)
                    select i.org_id, i.project_id, 'story', i.id, i.epic_id, %s, %s,
                           %s::jsonb, 'draft'
                    from public.issues i where i.id = %s
                    returning id
                    """,
                    (title, body, json.dumps(ac), run["issue_id"]),
                ).fetchone()
                story_ids.append(str(child["id"]))
                conn.execute(
                    """
                    insert into public.issue_events (org_id, issue_id, type, payload)
                    values (%s, %s, 'created', %s)
                    """,
                    (
                        run["org_id"],
                        child["id"],
                        json.dumps(
                            {"title": title, "parent_id": str(run["issue_id"])}
                        ),
                    ),
                )
        elif outcome == "succeeded" and kind == "code":
            # Agent-contributed test cases land in the project's library (US-1.16)
            for tc in test_cases or []:
                conn.execute(
                    """
                    insert into public.test_cases
                      (org_id, project_id, issue_id, title, steps, expected_result,
                       source, test_types, environments)
                    select i.org_id, i.project_id, i.id, %s, %s, %s,
                           'agent', %s, %s
                    from public.issues i where i.id = %s
                    """,
                    (
                        tc.get("title", "Untitled test"),
                        tc.get("steps", ""),
                        tc.get("expected_result", ""),
                        json.dumps(tc.get("test_types") or []),
                        json.dumps(tc.get("environments") or []),
                        run["issue_id"],
                    ),
                )

        payload: dict[str, Any] = {"run_id": run_id, "kind": kind}
        if error:
            payload["error"] = error
        if worker_name:
            payload["worker"] = worker_name  # audit trail names the worker (US-3.2)
        if trigger:
            payload["trigger"] = trigger  # 'submit' | 'lease-expiry' (US-3.4)
        if story_ids:
            payload["story_ids"] = story_ids  # US-2.33 breakdown summary event
        if run["issue_id"]:
            conn.execute(
                """
                insert into public.issue_events (org_id, issue_id, type, payload)
                values (%s, %s, %s, %s)
                """,
                (run["org_id"], run["issue_id"], event, json.dumps(payload)),
            )
        conn.commit()
        return True


def record_run_attempt(
    conn,
    org_id: str,
    issue_id: str | None,
    run_id: str | None,
    worker_id: str | None,
    kind: str,
    reason: str,
) -> None:
    """US-31.5: append one consumed attempt. Takes an open connection so it
    joins the caller's transaction — an attempt recorded for a requeue that
    then rolled back would block an item on work that never happened.

    Deliberately NOT derived from `runs`: requeue mutates the run row back to
    queued and nulls worker_id, so run rows cannot answer "how many times has
    this agent tried". Keyed on worker id, never name (US-32.2)."""
    if not issue_id:
        return
    conn.execute(
        """
        insert into public.run_attempts
          (org_id, issue_id, run_id, worker_id, kind, reason)
        values (%s, %s, %s, %s, %s, %s)
        """,
        (org_id, issue_id, run_id, worker_id, kind or "code", reason),
    )


def record_agent_failure(
    conn,
    run_id: str | None,
    category: str,
    *,
    worker_id: str | None = None,
    error: str | None = None,
    detail: dict[str, Any] | None = None,
    resumable: bool = False,
) -> None:
    """US-79.8: append one agent-failure report for the superadmin console.

    Snapshots agent identity (worker name/type, preset) at failure time —
    requeue reassigns the run row, so the run cannot answer "who failed"
    afterwards. The org/project/issue/kind/preset columns are read off the
    run row here, in the same transaction, before anything else mutates it
    further. Unlike `record_run_attempt` this captures issue-less runs too:
    a deploy or release-prep failure is still an agent failure.

    Runs under a savepoint so a failure to record never turns the requeue
    sweep or the run callback that noticed the failure into a crash — same
    posture as `self_report`.
    """
    if not run_id:
        return
    conn.execute("savepoint agent_failure")
    try:
        conn.execute(
            """
            insert into public.agent_failures
              (org_id, project_id, issue_id, run_id, kind, worker_id,
               worker_name, worker_type, preset_name, preset_version,
               category, error, detail, resumable)
            select r.org_id, r.project_id, r.issue_id, r.id, r.kind, %s::uuid,
                   coalesce(w.name, ''), coalesce(w.type, ''),
                   r.preset_name, r.preset_version,
                   %s, %s, %s, %s
            from public.runs r
            left join public.workers w on w.id = %s::uuid
            where r.id = %s
            """,
            (
                worker_id,
                category,
                error,
                json.dumps(detail or {}),
                resumable,
                worker_id,
                run_id,
            ),
        )
        conn.execute("release savepoint agent_failure")
    except Exception:  # noqa: BLE001
        conn.execute("rollback to savepoint agent_failure")
        logger.warning(
            "agent failure record failed for run %s", run_id, exc_info=True
        )


def release_attempt_block(
    settings: Settings, issue_id: str, actor: str | None = None
) -> bool:
    """US-31.5: the manager's explicit release — clears the block AND the
    attempt history, so the item starts counting again rather than blocking
    on the next failure. A decision with a record, not a silent retry."""
    if not _valid_uuid(issue_id):
        return False
    with _connect(settings) as conn:
        row = conn.execute(
            """
            update public.issues set attempts_blocked_at = null
            where id = %s and attempts_blocked_at is not null
            returning id, org_id
            """,
            (issue_id,),
        ).fetchone()
        if not row:
            return False
        spent = conn.execute(
            "delete from public.run_attempts where issue_id = %s returning id",
            (issue_id,),
        ).fetchall()
        conn.execute(
            """
            insert into public.issue_events (org_id, issue_id, type, payload)
            values (%s, %s, 'attempts-released', %s)
            """,
            (
                row["org_id"],
                issue_id,
                json.dumps({"cleared": len(spent), "actor": actor or "manager"}),
            ),
        )
        conn.commit()
    return True


def reset_issue_attempts(
    settings: Settings, issue_id: str, actor: str | None = None
) -> int:
    """US-36.2: clear an item's attempt history because a human is dispatching it.

    Distinct from `release_attempt_block` in two ways that matter. It acts
    whether or not the item is latched — so "I dispatched it" means the same
    thing regardless of a counter the manager cannot see from the dispatch
    button — and it is called by the manager's own dispatch rather than by a
    separate click.

    us-31.5 deliberately made releasing a separate action, to stop an AGENT
    looping unattended on an item it cannot finish. That reasoning holds for
    every automatic path, and `runs_refuse_on_exhausted_item` still enforces it
    there. It does not hold for a manager pressing Dispatch, which is itself the
    deliberate decision the second click was there to force.

    Returns how many attempts were cleared; 0 means there was nothing to clear
    and no event is written."""
    if not _valid_uuid(issue_id):
        return 0
    with _connect(settings) as conn:
        row = conn.execute(
            "select id, org_id from public.issues where id = %s", (issue_id,)
        ).fetchone()
        if not row:
            return 0
        spent = conn.execute(
            "delete from public.run_attempts where issue_id = %s returning id",
            (issue_id,),
        ).fetchall()
        conn.execute(
            "update public.issues set attempts_blocked_at = null where id = %s",
            (issue_id,),
        )
        if spent:
            # Only when something was actually cleared: a dispatch of a healthy
            # item should not litter its timeline.
            conn.execute(
                """
                insert into public.issue_events (org_id, issue_id, type, payload)
                values (%s, %s, 'attempts-released', %s)
                """,
                (
                    row["org_id"],
                    issue_id,
                    json.dumps(
                        {
                            "cleared": len(spent),
                            "actor": actor or "manager",
                            "via": "dispatch",
                        }
                    ),
                ),
            )
        conn.commit()
    return len(spent)


def issue_attempt_summary(settings: Settings, issue_id: str) -> dict[str, Any]:
    """US-31.5: what a blocked item says about itself — attempts spent, which
    agents spent them, and the last error verbatim (US-27.12: evidence before
    theory)."""
    if not _valid_uuid(issue_id):
        return {"attempts": 0, "blocked": False, "by_worker": [], "last_error": None}
    with _connect(settings) as conn:
        issue = conn.execute(
            "select org_id, attempts_blocked_at from public.issues where id = %s",
            (issue_id,),
        ).fetchone()
        if not issue:
            return {
                "attempts": 0,
                "blocked": False,
                "by_worker": [],
                "last_error": None,
            }
        rows = conn.execute(
            """
            select a.reason, a.created_at, a.worker_id,
                   coalesce(p.display_name, w.name, 'an agent') as worker_name
            from public.run_attempts a
            left join public.workers w on w.id = a.worker_id
            left join public.principals p on p.id = w.principal_id
            where a.issue_id = %s
            order by a.created_at desc
            """,
            (issue_id,),
        ).fetchall()
        ceiling = conn.execute(
            "select max_item_attempts from public.organizations where id = %s",
            (issue["org_id"],),
        ).fetchone()
        last_error = conn.execute(
            """
            select error from public.runs
            where issue_id = %s and error is not null
            order by finished_at desc nulls last, created_at desc
            limit 1
            """,
            (issue_id,),
        ).fetchone()
    by_worker: dict[str, int] = {}
    for r in rows:
        by_worker[r["worker_name"]] = by_worker.get(r["worker_name"], 0) + 1
    return {
        "attempts": len(rows),
        "ceiling": (ceiling or {}).get("max_item_attempts") or 5,
        "blocked": issue["attempts_blocked_at"] is not None,
        "blocked_at": issue["attempts_blocked_at"],
        "by_worker": [{"worker": k, "attempts": v} for k, v in by_worker.items()],
        "reasons": [{"reason": r["reason"], "at": r["created_at"]} for r in rows],
        "last_error": (last_error or {}).get("error"),
    }


def get_workspace_delivery(
    settings: Settings, worker_id: str, project_id: str
) -> dict[str, Any] | None:
    """US-31.6: the sha + path manifest this agent was last served for this
    project, or None if it has never been served."""
    if not (_valid_uuid(worker_id) and _valid_uuid(project_id)):
        return None
    with _connect(settings) as conn:
        row = conn.execute(
            """
            select base_sha, paths, served_at
            from public.workspace_deliveries
            where worker_id = %s and project_id = %s
            """,
            (worker_id, project_id),
        ).fetchone()
    if not row:
        return None
    return {
        "base_sha": row["base_sha"],
        "paths": list(row["paths"] or []),
        "served_at": row["served_at"],
    }


def record_workspace_delivery(
    settings: Settings,
    worker_id: str,
    project_id: str,
    org_id: str,
    base_sha: str,
    paths: list[str],
) -> None:
    """US-31.6: remember what was served. Called only AFTER the response has
    been built — recording a delivery the agent never received would make the
    next delta skip files it does not have."""
    with _connect(settings) as conn:
        conn.execute(
            """
            insert into public.workspace_deliveries
              (worker_id, project_id, org_id, base_sha, paths, served_at)
            values (%s, %s, %s, %s, %s, now())
            on conflict (worker_id, project_id) do update set
              base_sha = excluded.base_sha,
              paths = excluded.paths,
              served_at = now()
            """,
            (worker_id, project_id, org_id, base_sha, paths),
        )
        conn.commit()


def clear_workspace_delivery(
    settings: Settings, worker_id: str, project_id: str | None = None
) -> int:
    """US-31.8 tier-one repair: forget what we believe the agent holds, so the
    next get_workspace serves a full tree. Cheaper than deleting the folder —
    it keeps the dependencies and only re-establishes the source."""
    with _connect(settings) as conn:
        if project_id:
            rows = conn.execute(
                "delete from public.workspace_deliveries "
                "where worker_id = %s and project_id = %s returning worker_id",
                (worker_id, project_id),
            ).fetchall()
        else:
            rows = conn.execute(
                "delete from public.workspace_deliveries "
                "where worker_id = %s returning worker_id",
                (worker_id,),
            ).fetchall()
        conn.commit()
    return len(rows)


def fail_run_minimal(
    settings: Settings,
    run_id: str,
    error: str | None,
    worker_name: str | None = None,
) -> bool:
    """US-31.1: the smallest possible record of a failed run — status, error,
    one event. The fallback when complete_run's full bookkeeping raises: a
    failure report must land even when the trimmings cannot, because the
    alternative (observed 2026-07-26) is a run left `running` that loops on
    its lease forever. Deliberately no fan-out, no issue-status move — the
    reconciler and re-dispatch already treat a `failed` run correctly."""
    clean_error = (error or "").replace("\x00", "")[:8000] or "run failed"
    with _connect(settings) as conn:
        run = conn.execute(
            """
            update public.runs
            set status = 'failed', error = %s, finished_at = now()
            where id = %s and status = 'running'
            returning id, org_id, issue_id, kind
            """,
            (clean_error, run_id),
        ).fetchone()
        if not run:
            return False
        if run["issue_id"]:
            payload: dict[str, Any] = {
                "run_id": run_id,
                "kind": run.get("kind") or "code",
                "error": clean_error[:2000],
                "trigger": "submit-fallback",
            }
            if worker_name:
                payload["worker"] = worker_name
            conn.execute(
                """
                insert into public.issue_events (org_id, issue_id, type, payload)
                values (%s, %s, 'run-failed', %s)
                """,
                (run["org_id"], run["issue_id"], json.dumps(payload)),
            )
        conn.commit()
    return True


def dispatch_test_run(
    settings: Settings,
    issue_id: str,
    org_id: str,
    actor: str | None = None,
    instructions: str | None = None,
) -> dict[str, Any]:
    """US-13.11: queue a staffed verification pass over the issue's
    submitted code run. Guards: a succeeded code run with a branch must
    exist, and no test run may already be queued/running for the issue.
    Never touches issue status — the deliverable is per-case results.
    Returns {run_id} or {error}."""
    if not _valid_uuid(issue_id):
        return {"error": "issue not found"}
    with _connect(settings) as conn:
        issue = conn.execute(
            """
            select i.id, i.org_id, i.project_id, i.title, i.body,
                   i.acceptance_criteria, p.repo_full_name, p.default_branch
            from public.issues i
            join public.projects p on p.id = i.project_id
            where i.id = %s and i.org_id = %s
            """,
            (issue_id, org_id),
        ).fetchone()
        if not issue:
            return {"error": "issue not found"}
        code_run = conn.execute(
            """
            select id, branch_ref, pr_url from public.runs
            where issue_id = %s and kind = 'code' and status = 'succeeded'
              and branch_ref is not null
            order by finished_at desc nulls last limit 1
            """,
            (issue_id,),
        ).fetchone()
        if not code_run:
            return {
                "error": "no submitted code run with a branch to verify — "
                "dispatch a code run first"
            }
        test_cases = conn.execute(
            """
            select id, title, steps, expected_result from public.test_cases
            where issue_id = %s and status = 'active'
            order by created_at
            """,
            (issue_id,),
        ).fetchall()
        if not test_cases:
            return {
                "error": "this issue has no active test cases — author some "
                "in the plan/test-plan first, then send for verification"
            }
        active = conn.execute(
            """
            select id from public.runs
            where issue_id = %s and kind = 'test'
              and status in ('queued', 'running')
            limit 1
            """,
            (issue_id,),
        ).fetchone()
        if active:
            return {
                "error": "a test run is already queued or running for this "
                "work item"
            }
        input_context = {
            "run_kind": "test",
            "title": issue["title"],
            "story": issue["body"],
            "acceptance_criteria": issue["acceptance_criteria"],
            "branch_ref": code_run["branch_ref"],
            "code_run_id": str(code_run["id"]),
            "pr_url": code_run["pr_url"],
            "repo_full_name": issue["repo_full_name"],
            "default_branch": issue["default_branch"],
            "instructions": (instructions or "").strip() or None,
            "test_cases": [
                {
                    "id": str(t["id"]),
                    "title": t["title"],
                    "steps": t["steps"],
                    "expected_result": t["expected_result"],
                }
                for t in test_cases
            ],
        }
        run = conn.execute(
            """
            insert into public.runs
              (org_id, issue_id, provider, status, kind, input_context)
            values (%s, %s, 'claude', 'queued', 'test', %s)
            returning id
            """,
            (org_id, issue_id, json.dumps(input_context)),
        ).fetchone()
        conn.execute(
            """
            insert into public.issue_events (org_id, issue_id, type, payload)
            values (%s, %s, 'test-run-dispatched', %s)
            """,
            (
                org_id,
                issue_id,
                json.dumps(
                    {
                        "run_id": str(run["id"]),
                        "branch_ref": code_run["branch_ref"],
                        "actor": actor or "",
                        "case_count": len(test_cases),
                    }
                ),
            ),
        )
        conn.commit()
        return {"run_id": str(run["id"])}


def get_deployment_for_agent(
    settings: Settings, deployment_id: str, org_id: str
) -> dict[str, Any] | None:
    """US-13.13: the deployment + its server + project rows, service-side
    — the same shape deploy.launch consumes. Never includes credentials
    (those live in the data bucket, resolved inside the pipeline)."""
    if not _valid_uuid(deployment_id):
        return None
    with _connect(settings) as conn:
        dep = conn.execute(
            "select * from public.deployments where id = %s and org_id = %s",
            (deployment_id, org_id),
        ).fetchone()
        if not dep:
            return None
        # US-50.1: an external deployment has no server, by construction.
        server = (
            conn.execute(
                "select * from public.servers where id = %s",
                (dep["server_id"],),
            ).fetchone()
            if dep.get("server_id")
            else None
        )
        project = conn.execute(
            "select id, name, repo_full_name, uat_branch, production_branch "
            "from public.projects where id = %s",
            (dep["project_id"],),
        ).fetchone()
    return {
        "deployment": dict(dep),
        "server": dict(server) if server else None,
        "project": dict(project) if project else None,
    }


def agent_deploy_refusal(dep: dict[str, Any]) -> str | None:
    """US-13.13 rails, shared by dispatch AND the trigger tool (defense in
    depth): protected is human-only always; production needs the explicit
    per-deployment flag."""
    if dep.get("protected"):
        return "protected deployments are human-only — agents may never " \
               "run them"
    if dep.get("environment") == "production" and not dep.get(
        "agent_dispatch_allowed"
    ):
        return (
            "production deployments need the 'agent may deploy' flag on "
            "the deployment definition — a human sets it, audited"
        )
    return None


def dispatch_deploy_run(
    settings: Settings,
    deployment_id: str,
    org_id: str,
    ref: str | None = None,
    auto_rollback: bool = False,
    actor: str | None = None,
    release_branch: str | None = None,
) -> dict[str, Any]:
    """US-13.13: queue 'execute this deployment' as pool work. The agent
    triggers/observes/verifies/reports; the server deploys. Refuses:
    protected deployments, production without the flag, and a second
    concurrent deploy run for the same deployment."""
    bundle = get_deployment_for_agent(settings, deployment_id, org_id)
    if not bundle:
        return {"error": "deployment not found"}
    dep = bundle["deployment"]
    refusal = agent_deploy_refusal(dep)
    if refusal:
        return {"error": refusal}
    if ref and dep.get("protected"):
        return {"error": "ref overrides are not available on protected "
                "deployments"}
    with _connect(settings) as conn:
        active = conn.execute(
            """
            select id from public.runs
            where deployment_id = %s and kind = 'deploy'
              and status in ('queued', 'running')
            limit 1
            """,
            (deployment_id,),
        ).fetchone()
        if active:
            return {
                "error": "a deploy run is already queued or running for "
                "this deployment"
            }
        server = bundle["server"] or {}
        project = bundle["project"] or {}
        input_context = {
            "run_kind": "deploy",
            "deployment_id": str(dep["id"]),
            # Definition only — never credentials or secret values.
            "deployment": {
                "name": dep.get("name"),
                "environment": dep.get("environment"),
                # US-50.1/50.3: the kind changes what the run can do, so the
                # agent's instructions have to carry it.
                "kind": dep.get("kind") or "factory",
                "target_branch": dep.get("target_branch") or None,
                "server_name": server.get("name"),
                "branch": dep.get("branch"),
                "strategy": dep.get("strategy"),
                "website_url": dep.get("website_url"),
                "health_check_url": dep.get("health_check_url"),
            },
            "project_name": project.get("name"),
            "repo_full_name": project.get("repo_full_name"),
            "ref": (ref or "").strip() or None,
            # US-50.4: where a release is being promoted, the branch cut at
            # the pinned commit — an external merge takes it when it exists so
            # the pin survives, and the commit when it does not.
            "release_branch": (release_branch or "").strip() or None,
            "auto_rollback": bool(auto_rollback),
        }
        run = conn.execute(
            """
            insert into public.runs
              (org_id, project_id, issue_id, deployment_id, provider,
               status, kind, input_context)
            values (%s, %s, null, %s, 'claude', 'queued', 'deploy', %s)
            returning id
            """,
            (org_id, dep["project_id"], deployment_id, json.dumps(input_context)),
        ).fetchone()
        conn.commit()
        return {"run_id": str(run["id"])}


def dispatch_release_prep_for(
    settings: Settings,
    release_id: str,
    org_id: str,
    requested_by: str | None = None,
) -> dict[str, Any]:
    """US-63.1/63.3: queue release prep — the agent's whole job now is read
    the commit range and write the notes. Deploying to UAT, verifying health,
    and moving the release forward from there is deploy.py's own pipeline,
    triggered automatically the moment prep succeeds (US-63.2) — the agent
    is never handed deployment details to begin with.

    Still validates the UAT deployment up front (exists, not protected) so
    cutting fails fast rather than succeeding now and failing silently after
    notes are written.
    """
    if not _valid_uuid(release_id):
        return {"error": "release not found"}
    with _connect(settings) as conn:
        rel = conn.execute(
            "select * from public.releases where id = %s and org_id = %s",
            (release_id, org_id),
        ).fetchone()
        if not rel:
            return {"error": "release not found"}
        if rel["status"] in ("released", "rejected", "rolled-back", "cancelled"):
            return {"error": f'release is {rel["status"]} — cut a new one'}

        active = conn.execute(
            """
            select id from public.release_prep_runs
            where release_id = %s and status in ('queued', 'running')
            limit 1
            """,
            (release_id,),
        ).fetchone()
        if active:
            return {"error": "release prep is already queued or running"}

        deployment_id = conn.execute(
            "select release_uat_deployment_id from public.projects where id = %s",
            (rel["project_id"],),
        ).fetchone()
        deployment_id = deployment_id["release_uat_deployment_id"] if deployment_id else None
        if not deployment_id:
            return {
                "error": "this project has no UAT deployment designated for "
                "releases — set one on the Deployments tab"
            }
        bundle = get_deployment_for_agent(settings, str(deployment_id), org_id)
        if not bundle:
            return {"error": "the designated UAT deployment no longer exists"}
        # The same rails a deploy run answers to. A protected UAT deployment
        # is human-only, and that is not negotiable here either.
        refusal = agent_deploy_refusal(bundle["deployment"])
        if refusal:
            return {"error": refusal}

        # US-90.1: requested_by is null when the cut itself queued this
        # attempt, the manager's id when they clicked Retry.
        run = conn.execute(
            """
            insert into public.release_prep_runs
              (org_id, project_id, release_id, requested_by)
            values (%s, %s, %s, %s)
            returning id
            """,
            (org_id, rel["project_id"], release_id, requested_by),
        ).fetchone()
        conn.execute(
            "update public.releases set status = 'queued', updated_at = now() "
            "where id = %s",
            (release_id,),
        )
        conn.commit()
        return {"run_id": str(run["id"])}


def count_release_attempts(settings: Settings, release_id: str) -> dict[str, int]:
    """US-90.1: how many times each leg has been tried, so a retry response
    reports "attempt N" honestly instead of presenting a clean first try."""
    if not _valid_uuid(release_id):
        return {"prep": 0, "deploy": 0}
    with _connect(settings) as conn:
        prep = conn.execute(
            "select count(*) as n from public.release_prep_runs where release_id = %s",
            (release_id,),
        ).fetchone()
        dep = conn.execute(
            "select count(*) as n from public.deployment_runs where release_id = %s",
            (release_id,),
        ).fetchone()
    return {"prep": int(prep["n"]), "deploy": int(dep["n"])}


def update_release(
    settings: Settings, release_id: str, patch: dict[str, Any]
) -> dict[str, Any] | None:
    """US-21.3: service-side release update, called from the MCP submit path
    where the caller is a worker holding the run, not a session user."""
    if not _valid_uuid(release_id) or not patch:
        return None
    cols = ", ".join(f"{k} = %s" for k in patch)
    with _connect(settings) as conn:
        row = conn.execute(
            f"update public.releases set {cols}, updated_at = now() "
            "where id = %s returning *",
            (*patch.values(), release_id),
        ).fetchone()
        conn.commit()
        return row


def stamp_release_milestones(
    settings: Settings,
    release_id: str,
    *,
    notes_written: bool = False,
    uat_deployed: bool = False,
    cases_attached: bool = False,
) -> None:
    """US-21.3: record what a release has reached, once.

    `coalesce` so a re-dispatched run that redoes a step does not rewrite the
    original timestamp — the milestone is when it FIRST happened, and the
    timeline (us-21.6) reads it as such.
    """
    if not _valid_uuid(release_id):
        return
    sets, args = [], []
    for flag, col in (
        (notes_written, "notes_written_at"),
        (uat_deployed, "uat_deployed_at"),
        (cases_attached, "cases_attached_at"),
    ):
        if flag:
            sets.append(f"{col} = coalesce({col}, now())")
    if not sets:
        return
    args.append(release_id)
    with _connect(settings) as conn:
        conn.execute(
            f"update public.releases set {', '.join(sets)}, updated_at = now() "
            "where id = %s",
            tuple(args),
        )
        conn.commit()


def attach_release_test_cases(
    settings: Settings,
    *,
    release_id: str,
    org_id: str,
    project_id: str,
    cases: list[dict[str, Any]],
) -> int:
    """US-21.3/21.4: attach agent-authored regression cases to a release.

    The agent only ever ADDS. It cannot edit or remove a case, because a case
    it could delete is coverage an approved test plan promised.
    """
    if not _valid_uuid(release_id) or not cases:
        return 0
    rows = []
    for c in cases:
        title = str(c.get("title") or "").strip()
        if not title:
            continue
        rows.append(
            (
                org_id,
                project_id,
                release_id,
                title[:500],
                str(c.get("steps") or "").strip(),
                str(c.get("expected_result") or "").strip(),
                "agent",
            )
        )
    if not rows:
        return 0
    with _connect(settings) as conn:
        for row in rows:
            conn.execute(
                """
                insert into public.test_cases
                  (org_id, project_id, release_id, title, steps,
                   expected_result, source)
                values (%s, %s, %s, %s, %s, %s, %s)
                """,
                row,
            )
        conn.commit()
    return len(rows)


def attach_release_inherited_cases(settings: Settings, release_id: str) -> int:
    """US-21.4: copy every included work item's active test cases onto the
    release. Idempotent, so a re-dispatched run does not duplicate them."""
    if not _valid_uuid(release_id):
        return 0
    with _connect(settings) as conn:
        row = conn.execute(
            "select public.attach_release_inherited_cases(%s) as n", (release_id,)
        ).fetchone()
        conn.commit()
        return int((row or {}).get("n") or 0)


def update_run_input_context(
    settings: Settings, run_id: str, patch: dict[str, Any]
) -> None:
    """Merge keys into a run's input_context (US-13.13: the deploy run
    records the deployment_run it triggered, rollback bookkeeping, and
    the final verdict there)."""
    if not _valid_uuid(run_id):
        return
    with _connect(settings) as conn:
        conn.execute(
            "update public.runs set input_context = input_context || %s::jsonb "
            "where id = %s",
            (json.dumps(patch), run_id),
        )
        conn.commit()


def get_deployment_run_view(
    settings: Settings, deployment_run_id: str, org_id: str
) -> dict[str, Any] | None:
    """US-13.13: one deployment run's observable state — status, timing,
    and a log tail (never env values; the pipeline masks secrets as it
    writes the log)."""
    if not _valid_uuid(deployment_run_id):
        return None
    with _connect(settings) as conn:
        row = conn.execute(
            """
            select id, deployment_id, kind, status, started_at, finished_at,
                   commit_sha, release_path, right(log, 2000) as log_tail
            from public.deployment_runs
            where id = %s and org_id = %s
            """,
            (deployment_run_id, org_id),
        ).fetchone()
    return row


def latest_successful_deployment_run(
    settings: Settings, deployment_id: str, exclude_run_id: str | None = None
) -> dict[str, Any] | None:
    """US-13.13: the rollback target — the most recent succeeded run with
    a retained release, excluding the run being rolled back."""
    with _connect(settings) as conn:
        row = conn.execute(
            """
            select * from public.deployment_runs
            where deployment_id = %s and status = 'succeeded'
              and release_path is not null
              and (%s::uuid is null or id <> %s::uuid)
            order by created_at desc limit 1
            """,
            (deployment_id, exclude_run_id, exclude_run_id),
        ).fetchone()
    return row


def count_run_test_results(settings: Settings, run_id: str) -> int:
    """US-13.11: how many per-case results this run has reported — a test
    run completing with zero has nothing to hand back and must release
    instead."""
    if not _valid_uuid(run_id):
        return 0
    with _connect(settings) as conn:
        row = conn.execute(
            """
            select count(*) as n
            from public.test_run_results trr
            join public.test_runs tr on tr.id = trr.test_run_id
            where tr.run_id = %s
            """,
            (run_id,),
        ).fetchone()
    return int(row["n"] or 0)


def child_story_ids(settings: Settings, issue_id: str) -> list[str]:
    """US-2.33: ids of a feature's child stories — used to score the
    freshly created breakdown children off the submit critical path."""
    with _connect(settings) as conn:
        rows = conn.execute(
            "select id from public.issues "
            "where parent_id = %s and type = 'story'",
            (issue_id,),
        ).fetchall()
    return [str(r["id"]) for r in rows]


def reap_orphaned_provider_runs(settings: Settings) -> int:
    """Reap provider runs stranded by a hard kill of the runner or API
    (US-2.15, reshaped by US-3.2). Worker-claimed runs with an expired
    lease return to the pool (claimed-but-abandoned work is retryable,
    not dead); legacy runner runs still 'running' predate this process
    and fail, their issues returning to 'failed' for re-dispatch.
    'queued' rows are the pool (US-3.2) and are never reaped."""
    requeued = requeue_expired_claims(settings)
    with _connect(settings) as conn:
        rows = conn.execute(
            """
            update public.runs
            set status = 'failed', finished_at = now(),
                error = coalesce(error, 'interrupted — runner or API died mid-run; reaped at startup')
            where status = 'running' and worker_id is null
            returning id, org_id, issue_id, kind
            """,
        ).fetchall()
        for row in rows:
            # us-96.6: the think-phase kinds whose failure never moves the
            # issue (see perform_submit's exemption) are exempt here too —
            # the reaper previously forced prd/breakdown issues to 'failed'
            # unconditionally, the second dead end lifecycles.md documented.
            if row["kind"] not in ("prd", "test", "elaborate", "breakdown"):
                conn.execute(
                    "update public.issues set status = 'failed' where id = %s",
                    (row["issue_id"],),
                )
            conn.execute(
                """
                insert into public.issue_events (org_id, issue_id, type, payload)
                values (%s, %s, 'run-failed', %s)
                """,
                (
                    row["org_id"],
                    row["issue_id"],
                    json.dumps(
                        {
                            "run_id": str(row["id"]),
                            "kind": row["kind"],
                            "error": "orphaned run reaped at API startup",
                        }
                    ),
                ),
            )
        conn.commit()
        return requeued + len(rows)


# ------------------------------------------------ worker pool (US-3.2)

# US-39.2: 15 minutes was the autonomous default and it was far too short for
# real coding work -- `timeout_from_lease` turns it into ~13.5 minutes of CLI
# time, so an agent with no configured allowance was being cut off before it
# could finish anything substantial. Raised to 120 minutes on request.
#
# This is the PER-STORY default: a batch run multiplies it by
# run_work_units() and bounds the product with max_total_run_minutes.
_LEASES = {"human": "24 hours", "autonomous": "120 minutes"}
_LEASE_SECONDS = {"human": 86400, "autonomous": 7200}

# The bound on the product when no max_total_run_minutes is configured. Matches
# the column's own check constraint, so an unset ceiling and the highest
# settable one agree.
DEFAULT_TOTAL_RUN_MINUTES = 1440

# US-39.2: the same idea for turns. `max_turns` is multiplied by the work units,
# and this bounds the product. Deliberately generous -- it is a runaway guard,
# not an allowance -- and deliberately NOT the preset's 1..500 validation range,
# which governs what a human may type rather than what a batch resolves to.
MAX_SCALED_TURNS = 2000


def worker_lease_seconds(
    settings: Settings,
    worker_id: str,
    worker_type: str,
    run_id: str | None = None,
) -> int:
    """US-31.2: the lease this worker's next claim would get, in seconds —
    served in the work-context bundle so the runner can set its CLI limit
    strictly BELOW the time it actually holds the run.

    US-39.2: when the run is known, this reports the lease the claim ACTUALLY
    wrote rather than recomputing it. The two calculations used to live apart —
    `claim_run` in SQL, this in Python — and agreed only by coincidence. Once
    the claim started scaling with the work, a parallel calculation here would
    have told the runner it had time the claim would not honour; the claim would
    then expire mid-run and requeue the work, and the failure would look like
    the agent dying. One source of truth instead.
    """
    if run_id and _valid_uuid(run_id):
        with _connect(settings) as conn:
            row = conn.execute(
                "select greatest(0, extract(epoch from (claim_expires_at - now())))::int"
                " as remaining from public.runs where id = %s and claim_expires_at is not null",
                (run_id,),
            ).fetchone()
        if row and row.get("remaining"):
            return int(row["remaining"])
    with _connect(settings) as conn:
        row = conn.execute(
            "select max_run_minutes from public.runner_config where worker_id = %s",
            (worker_id,),
        ).fetchone()
    if row and row.get("max_run_minutes"):
        return int(row["max_run_minutes"]) * 60
    return _LEASE_SECONDS.get(worker_type, _LEASE_SECONDS["autonomous"])


def run_work_units(settings: Settings, run_id: str) -> int:
    """US-39.2: how many stories this run carries. Server-side, never supplied
    by the runner — it is the thing being limited."""
    if not _valid_uuid(run_id):
        return 1
    try:
        with _connect(settings) as conn:
            row = conn.execute(
                "select public.run_work_units(%s) as units", (run_id,)
            ).fetchone()
        return max(1, int(row["units"])) if row else 1
    except Exception:  # noqa: BLE001 — an uncountable batch is one unit, never zero
        logger.warning("could not count work units for run %s", run_id, exc_info=True)
        return 1


def lease_for_worker_type(worker_type: str) -> str:
    """US-27.2: the lease an agent of this type gets, so `get_instructions`
    can state it rather than leaving the agent to discover it by losing a
    claim mid-run."""
    return _LEASES.get(worker_type, _LEASES["autonomous"])


def _valid_uuid(value: str) -> bool:
    try:
        _uuid_mod.UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


# US-55.1: there is no run-kind → capability mapping any more. The US-13.10
# matrix (seven capability columns, us-43.1/44.1's guidelines/elaborate
# riding `plan`) is retired: a worker_capabilities row now means project
# ACCESS, and what an agent does is its own kind checkboxes
# (runner_config.enabled_kinds), which carry all ten kinds first-class.
# `public.worker_has_grant` (migration 199) is the one predicate for both.


# US-87.7: a heartbeat is not a write storm.
#
# Authenticating a worker used to be an UPDATE — "the single auth path for
# every /worker/* call and the git remote" — and it ran ~940,000 times over
# six weeks on prod. Every one produced a WAL record (feeding the Realtime
# decoder us-87.5 exists to quiet) and took a row lock, so one worker's
# concurrent requests serialized on their own presence timestamp. A polling
# worker paid it on every poll.
#
# Presence does not need write durability on the authentication path: the
# only thing that reads `workers.last_seen_at` is the "is it online?"
# judgement, and that window is WORKER_ONLINE_MINUTES = 5 (see
# apps/web/.../workbench/data.ts). No database function reads it at all —
# checked against pg_proc on prod, 2026-08-12. Writing at most once every
# 15 seconds therefore leaves a 20x margin: a worker that is alive can never
# be reported offline because its heartbeat was throttled (us-87.7 AC3).
#
# The throttle is per-process and in memory. A restart loses it, which costs
# at most one extra write per worker — never a stale-forever timestamp
# (AC4), because a lost entry means the next call writes rather than skips.
LAST_SEEN_INTERVAL_S = 15.0
_last_seen_written: dict[str, float] = {}
_last_seen_lock = threading.Lock()


def _touch_worker_last_seen(settings: Settings, worker_id: str) -> None:
    """Record presence at most once per worker per LAST_SEEN_INTERVAL_S."""
    now = time.monotonic()
    with _last_seen_lock:
        previous = _last_seen_written.get(worker_id)
        if previous is not None and now - previous < LAST_SEEN_INTERVAL_S:
            return
        _last_seen_written[worker_id] = now
    try:
        with _connect(settings) as conn:
            conn.execute(
                "update public.workers set last_seen_at = now() where id = %s",
                (worker_id,),
            )
            conn.commit()
    except Exception:  # noqa: BLE001 — presence must never fail an auth
        # Forget the mark so the next call retries rather than waiting out
        # the interval on a write that never landed.
        with _last_seen_lock:
            if _last_seen_written.get(worker_id) == now:
                del _last_seen_written[worker_id]
        logger.debug("worker last_seen_at update failed", exc_info=True)


def get_worker_by_token(settings: Settings, token: str) -> dict[str, Any] | None:
    """Active worker for a token — the single auth path for every /worker/*
    call (US-3.2) and the git remote (US-3.8).

    US-87.7: this READS. Presence is recorded separately and throttled; see
    `_touch_worker_last_seen` above for why that is safe."""
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with _connect(settings) as conn:
        row = conn.execute(
            """
            select id, org_id, name, type, status, principal_id, project_id,
                   no_claim_checkout
            from public.workers
            where token_hash = %s and status = 'active'
            """,
            (token_hash,),
        ).fetchone()
    if row is not None:
        _touch_worker_last_seen(settings, str(row["id"]))
    return row


# --------------------------------------------------------------------------
# Supervisor runner presence (US-10.1)
# --------------------------------------------------------------------------


def open_runner_session(
    settings: Settings,
    worker_id: str,
    org_id: str,
    host_info: dict[str, Any] | None = None,
    agent_versions: dict[str, Any] | None = None,
    modules_available: list[str] | None = None,
    module_settings: list[dict[str, Any]] | None = None,
) -> str:
    """Record a new control-socket connection and return its id. Any prior
    live session for the worker is closed first — a worker holds one socket,
    so a reconnect supersedes a session the server never saw drop."""
    with _connect(settings) as conn:
        conn.execute(
            """
            update public.runner_sessions
            set disconnected_at = now()
            where worker_id = %s and disconnected_at is null
            """,
            (worker_id,),
        )
        row = conn.execute(
            """
            insert into public.runner_sessions
              (org_id, worker_id, host_info, agent_versions, modules_available,
               module_settings)
            values (%s, %s, %s, %s, %s, %s)
            returning id
            """,
            (
                org_id,
                worker_id,
                json.dumps(host_info or {}),
                json.dumps(agent_versions or {}),
                list(modules_available or []),
                # US-32.4: what each of those modules can be told.
                json.dumps(module_settings or []),
            ),
        ).fetchone()
        conn.commit()
    return str(row["id"])


def touch_runner_session(settings: Settings, session_id: str) -> None:
    """Heartbeat a live session (last_seen_at = now); no-op once disconnected."""
    with _connect(settings) as conn:
        conn.execute(
            """
            update public.runner_sessions
            set last_seen_at = now()
            where id = %s and disconnected_at is null
            """,
            (session_id,),
        )
        conn.commit()


def close_runner_session(settings: Settings, session_id: str) -> None:
    """Mark a session disconnected (idempotent)."""
    with _connect(settings) as conn:
        conn.execute(
            """
            update public.runner_sessions
            set disconnected_at = now()
            where id = %s and disconnected_at is null
            """,
            (session_id,),
        )
        conn.commit()


def get_worker(settings: Settings, worker_id: str) -> dict[str, Any] | None:
    """A worker row by id (for the config endpoint's org resolution)."""
    with _connect(settings) as conn:
        return conn.execute(
            "select id, org_id, name, type, status from public.workers where id = %s",
            (worker_id,),
        ).fetchone()


# --------------------------------------------------------------------------
# Agent identity (US-32.2)
# --------------------------------------------------------------------------


def agent_identity(settings: Settings, principal_id: str) -> dict[str, Any] | None:
    """Everything that carries an agent's name, plus the ids that must not move.

    The generated `pod-001-1` is copied into three columns at provision time
    (`principals.display_name`, `workers.name`, `agent_slots.name`), so a rename
    that writes one of them leaves the other two lying. This is the read half of
    the fan-out; `rename_agent` is the write half.
    """
    if not _valid_uuid(principal_id):
        return None
    with _connect(settings) as conn:
        return conn.execute(
            """
            select p.id           as principal_id,
                   p.kind         as kind,
                   p.display_name as display_name,
                   w.id           as worker_id,
                   w.org_id       as org_id,
                   w.name         as worker_name,
                   s.id           as slot_id,
                   s.name         as slot_name,
                   s.slot_index   as slot_index,
                   s.service_name as service_name
            from public.principals p
            left join public.workers w
              on w.principal_id = p.id and w.type = 'autonomous'
            left join public.agent_slots s
              on s.principal_id = p.id and s.status = 'active'
            where p.id = %s
            limit 1
            """,
            (principal_id,),
        ).fetchone()


def rename_agent(
    settings: Settings,
    principal_id: str,
    name: str,
    *,
    actor_id: str | None,
    actor_email: str,
    org_id: str,
) -> dict[str, Any]:
    """US-32.2: one rename, every name column, in one transaction.

    `service_name`, `slot_index`, `workspace_path`, `worker_id` and
    `principal_id` are deliberately untouched: a systemd unit named after a
    display name is one rename away from an orphaned service.
    """
    with _connect(settings) as conn:
        before = conn.execute(
            "select display_name from public.principals where id = %s",
            (principal_id,),
        ).fetchone()
        conn.execute(
            "update public.principals set display_name = %s where id = %s",
            (name, principal_id),
        )
        conn.execute(
            "update public.workers set name = %s where principal_id = %s",
            (name, principal_id),
        )
        conn.execute(
            "update public.agent_slots set name = %s "
            "where principal_id = %s and status = 'active'",
            (name, principal_id),
        )
        conn.execute(
            """
            insert into public.agent_events
              (org_id, principal_id, type, payload, actor_id, actor_email)
            values (%s, %s, 'renamed', %s::jsonb, %s, %s)
            """,
            (
                org_id,
                principal_id,
                json.dumps(
                    {"from": (before or {}).get("display_name"), "to": name}
                ),
                actor_id,
                actor_email or "",
            ),
        )
        conn.commit()
    return {"name": name, "from": (before or {}).get("display_name")}


# --------------------------------------------------------------------------
# Run-setting presets (US-32.5)
# --------------------------------------------------------------------------


def org_module_support(settings: Settings, org_id: str) -> dict[str, set[str]]:
    """Which settings the modules ENABLED somewhere in this org can express.

    Built from us-32.4's declarations, joined to the modules each agent has
    actually enabled — a knob declared by a module nobody enabled is no help to
    a preset, and a module enabled on an agent that has never connected has
    declared nothing yet, so it contributes nothing rather than a guess.
    """
    with _connect(settings) as conn:
        enabled_rows = conn.execute(
            "select distinct unnest(enabled_modules) as module "
            "from public.runner_config where org_id = %s",
            (org_id,),
        ).fetchall()
        # The most recent session per worker, connected or not.
        decl_rows = conn.execute(
            """
            select distinct on (worker_id) worker_id, module_settings
            from public.runner_sessions
            where org_id = %s
            order by worker_id, connected_at desc
            """,
            (org_id,),
        ).fetchall()
    enabled = {r["module"] for r in enabled_rows if r["module"]}
    support: dict[str, set[str]] = {}
    for row in decl_rows:
        for entry in row["module_settings"] or []:
            name = (entry or {}).get("module")
            if not name or name not in enabled:
                continue
            support.setdefault(name, set()).update(
                str(k.get("name")) for k in (entry.get("settings") or []) if k.get("name")
            )
    return support


def list_presets(
    settings: Settings, org_id: str, include_archived: bool = False
) -> list[dict[str, Any]]:
    with _connect(settings) as conn:
        return conn.execute(
            """
            select id, org_id, template_key, seeded_version, name, description,
                   model, settings, version, sort_order, archived_at, tool_grants
            from public.agent_presets
            where org_id = %s
              and (%s or archived_at is null)
            order by sort_order asc, name asc
            """,
            (org_id, include_archived),
        ).fetchall()


def get_preset(settings: Settings, preset_id: str) -> dict[str, Any] | None:
    if not _valid_uuid(preset_id):
        return None
    with _connect(settings) as conn:
        return conn.execute(
            """
            select id, org_id, template_key, seeded_version, name, description,
                   model, settings, version, sort_order, archived_at, tool_grants
            from public.agent_presets where id = %s
            """,
            (preset_id,),
        ).fetchone()


def create_preset(
    settings: Settings,
    org_id: str,
    *,
    name: str,
    description: str,
    model: str | None,
    preset_settings: dict[str, Any],
) -> dict[str, Any]:
    with _connect(settings) as conn:
        row = conn.execute(
            """
            insert into public.agent_presets
              (org_id, name, description, model, settings, sort_order)
            values (%s, %s, %s, %s, %s::jsonb,
                    coalesce((select max(sort_order) + 10
                              from public.agent_presets where org_id = %s), 100))
            returning id, org_id, template_key, seeded_version, name,
                      description, model, settings, version, sort_order,
                      archived_at
            """,
            (org_id, name, description, model, json.dumps(preset_settings), org_id),
        ).fetchone()
        conn.commit()
    return row


def update_preset(
    settings: Settings,
    preset_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    model: str | None = None,
    clear_model: bool = False,
    preset_settings: dict[str, Any] | None = None,
    tool_grants: list[str] | None = None,
) -> dict[str, Any] | None:
    """Patch a preset; the version trigger bumps only if what it DOES changed."""
    with _connect(settings) as conn:
        row = conn.execute(
            """
            update public.agent_presets set
              name        = coalesce(%s, name),
              description = coalesce(%s, description),
              model       = case when %s then null else coalesce(%s, model) end,
              settings    = coalesce(%s::jsonb, settings),
              tool_grants = coalesce(%s::uuid[], tool_grants)
            where id = %s
            returning id, org_id, template_key, seeded_version, name,
                      description, model, settings, version, sort_order,
                      archived_at, tool_grants
            """,
            (
                name,
                description,
                clear_model,
                model,
                json.dumps(preset_settings) if preset_settings is not None else None,
                tool_grants,
                preset_id,
            ),
        ).fetchone()
        conn.commit()
    return row


def archive_preset(settings: Settings, preset_id: str) -> bool:
    """Archived, never deleted: a finished run names the preset it ran under."""
    with _connect(settings) as conn:
        row = conn.execute(
            "update public.agent_presets set archived_at = now() "
            "where id = %s and archived_at is null returning id",
            (preset_id,),
        ).fetchone()
        conn.commit()
    return row is not None


def org_default_preset(settings: Settings, org_id: str) -> dict[str, Any] | None:
    """US-32.6: what an unset route inherits. Balanced, unless changed."""
    with _connect(settings) as conn:
        return conn.execute(
            "select id, name, model, settings, version, tool_grants "
            "from public.agent_presets "
            "where org_id = %s and is_default and archived_at is null limit 1",
            (org_id,),
        ).fetchone()


def presets_by_id(settings: Settings, org_id: str) -> dict[str, dict[str, Any]]:
    """Every live preset in the org, keyed by id — the resolver's lookup."""
    with _connect(settings) as conn:
        rows = conn.execute(
            "select id, name, model, settings, version, tool_grants "
            "from public.agent_presets where org_id = %s and archived_at is null",
            (org_id,),
        ).fetchall()
    return {str(r["id"]): dict(r) for r in rows}


def preset_usage(settings: Settings, org_id: str) -> dict[str, int]:
    """How many agents point a route at each preset.

    US-32.6: editing `Deep` changes every agent whose Code row references it —
    that is the reason to have presets, and it is also why the blast radius has
    to be visible before the edit rather than discovered after it.
    """
    with _connect(settings) as conn:
        rows = conn.execute(
            """
            select entry.value->>'preset_id' as preset_id, count(*) as n
            from public.runner_config c,
                 jsonb_each(coalesce(c.run_routes, '{}'::jsonb)) entry
            where c.org_id = %s and entry.value ? 'preset_id'
            group by 1
            """,
            (org_id,),
        ).fetchall()
    return {r["preset_id"]: r["n"] for r in rows if r["preset_id"]}


def record_run_fault_class(
    settings: Settings, run_id: str, fault_class: str | None
) -> None:
    """US-33.4: store the classification the runner already reported.

    us-10.11 has classified every failure as work-fault or runner-fault since it
    shipped, and the runner has always sent it — nothing ever stored it. Escalation
    needs it: a broken box is not answered by thinking harder.
    """
    if not _valid_uuid(run_id) or fault_class not in ("work-fault", "runner-fault"):
        return
    with _connect(settings) as conn:
        conn.execute(
            "update public.runs set fault_class = %s where id = %s",
            (fault_class, run_id),
        )
        conn.commit()


def record_run_settings(
    settings: Settings,
    run_id: str,
    record: dict[str, Any],
) -> None:
    """US-32.7: stamp the resolved settings on the run, once, at claim.

    Written as values plus provenance rather than a preset reference, so a
    completed run reports the same thing forever — editing or deleting the
    preset afterwards cannot rewrite history.
    """
    if not _valid_uuid(run_id):
        return
    # US-52.4 → US-53.1: `billing` is decided by the CALLER from the agent's
    # own config (`runner_config.claude_billing`) and whether its live session
    # can honor subscription mode — never from the resolved settings, which no
    # longer carry billing at all. `subscription` marks the run deliberately
    # off-meter; zero llm_usage rows is correct for it, not a metering gap.
    resolved_values = record.get("resolved_settings") or {}
    billing = record.get("billing") or "metered"
    with _connect(settings) as conn:
        conn.execute(
            """
            update public.runs set
              resolved_settings = %s::jsonb,
              settings_sources  = %s::jsonb,
              preset_id         = %s,
              preset_name       = %s,
              preset_version    = %s,
              billing           = %s
            where id = %s
            """,
            (
                json.dumps(resolved_values),
                json.dumps(record.get("settings_sources") or {}),
                record.get("preset_id"),
                record.get("preset_name"),
                record.get("preset_version"),
                billing,
                run_id,
            ),
        )
        conn.commit()


def list_preset_templates(settings: Settings) -> list[dict[str, Any]]:
    with _connect(settings) as conn:
        return conn.execute(
            "select id, key, name, description, settings, model_hint, "
            "sort_order, version, updated_at from public.preset_templates "
            "order by sort_order asc, name asc"
        ).fetchall()


def get_preset_template(settings: Settings, key: str) -> dict[str, Any] | None:
    with _connect(settings) as conn:
        return conn.execute(
            "select id, key, name, description, settings, model_hint, "
            "sort_order, version from public.preset_templates where key = %s",
            (key,),
        ).fetchone()


def update_preset_template(
    settings: Settings,
    key: str,
    *,
    name: str | None,
    description: str | None,
    model_hint: str | None,
    template_settings: dict[str, Any] | None,
    updated_by: str | None,
) -> dict[str, Any] | None:
    """A superadmin edit. It does NOT touch any org's presets — each org is
    offered the change as an explicit re-seed that states its effect."""
    with _connect(settings) as conn:
        row = conn.execute(
            """
            update public.preset_templates set
              name        = coalesce(%s, name),
              description = coalesce(%s, description),
              model_hint  = coalesce(%s, model_hint),
              settings    = coalesce(%s::jsonb, settings),
              updated_by  = coalesce(%s, updated_by)
            where key = %s
            returning id, key, name, description, settings, model_hint,
                      sort_order, version
            """,
            (
                name,
                description,
                model_hint,
                json.dumps(template_settings) if template_settings is not None else None,
                updated_by,
                key,
            ),
        ).fetchone()
        conn.commit()
    return row


def apply_reseed(
    settings: Settings, org_id: str, keys: list[str]
) -> list[dict[str, Any]]:
    """Copy the named templates onto this org's seeded copies of them.

    Only rows whose `template_key` matches: a preset the org invented is never
    touched, and neither is one whose provenance was deliberately cut.
    """
    if not keys:
        return []
    with _connect(settings) as conn:
        rows = conn.execute(
            """
            update public.agent_presets p set
              description    = t.description,
              settings       = t.settings,
              seeded_version = t.version
            from public.preset_templates t
            where p.template_key = t.key
              and p.org_id = %s
              and p.template_key = any(%s)
              and p.archived_at is null
            returning p.id, p.name, p.template_key, p.version, p.seeded_version
            """,
            (org_id, list(keys)),
        ).fetchall()
        # Every org gets any template it has no copy of at all — a template
        # added after the org was created should still reach it.
        conn.execute("select public.seed_org_presets(%s)", (org_id,))
        conn.commit()
    return list(rows)


# --------------------------------------------------------------------------
# Supervisor runner config (US-10.2)
# --------------------------------------------------------------------------

_RUNNER_CONFIG_DEFAULTS: dict[str, Any] = {
    "enabled_modules": [],
    "model_routes": {},
    # US-32.6: per run kind, a preset reference or inline custom settings.
    "run_routes": {},
    "autonomy_policy": {},
    # US-26.5: a paused runner stays connected and configured but is offered
    # no work. Unset means not paused — an agent installed by hand behaves
    # exactly as it did before Phase 26.
    "paused": False,
    # US-31.2: null = the worker-type default lease (15 min autonomous).
    "max_run_minutes": None,
    # US-31.5: attempts this agent may spend on one work item.
    "max_item_attempts": 3,
    # US-53.1: how this agent's Claude runs are billed. One switch, on the
    # agent — never a preset or route setting.
    "claude_billing": "api",
    # US-53.4: null = all kinds (pre-checkbox behavior); a list is explicit.
    "enabled_kinds": None,
    # US-66.1: per-kind model this agent pins, org-owned unlike the six
    # platform-owned fields above.
    "model_overrides": {},
}


def worker_session_declares_auth(settings: Settings, worker_id: str) -> bool:
    """US-53.1: whether the worker's LIVE session declared the claude `auth`
    knob — i.e. its supervisor understands subscription mode. The stamp uses
    this so a run on an old runner is recorded `metered` (which is what that
    runner will actually do), never mislabelled off-meter."""
    with _connect(settings) as conn:
        row = conn.execute(
            """
            select 1
            from public.runner_sessions rs,
                 jsonb_array_elements(rs.module_settings) m,
                 jsonb_array_elements(m->'settings') k
            where rs.worker_id = %s and rs.disconnected_at is null
              and m->>'module' = 'claude' and k->>'name' = 'auth'
            limit 1
            """,
            (worker_id,),
        ).fetchone()
    return bool(row)


def worker_is_paused(settings: Settings, worker_id: str) -> bool:
    """US-26.5: pause without revoking.

    workers.status is only active|revoked, and revoking would invalidate the
    token written to the agent machine — the agent would drop off entirely and
    could not be resumed without a re-provision. So pause is its own column,
    read here on every pool listing and every claim.
    """
    with _connect(settings) as conn:
        row = conn.execute(
            "select paused from public.runner_config where worker_id = %s",
            (worker_id,),
        ).fetchone()
    return bool(row and row["paused"])


def get_runner_config(settings: Settings, worker_id: str) -> dict[str, Any]:
    """Resolved server-side config for a runner, defaults when unset (US-10.2)."""
    with _connect(settings) as conn:
        row = conn.execute(
            """
            select enabled_modules, model_routes, run_routes, autonomy_policy,
                   paused, max_run_minutes, max_total_run_minutes,
                   max_item_attempts, claude_billing, enabled_kinds,
                   model_overrides
            from public.runner_config where worker_id = %s
            """,
            (worker_id,),
        ).fetchone()
    if not row:
        return dict(_RUNNER_CONFIG_DEFAULTS)
    return {
        "enabled_modules": list(row["enabled_modules"]),
        "model_routes": row["model_routes"],
        "run_routes": row["run_routes"],
        "autonomy_policy": row["autonomy_policy"],
        "paused": bool(row["paused"]),
        "max_run_minutes": row["max_run_minutes"],
        # US-39.2: the ceiling on per-story x stories carried.
        "max_total_run_minutes": row["max_total_run_minutes"],
        "max_item_attempts": row["max_item_attempts"],
        "claude_billing": row["claude_billing"],
        "enabled_kinds": row["enabled_kinds"],
        # US-66.1: per-kind model this agent pins.
        "model_overrides": row["model_overrides"] or {},
    }


def upsert_runner_config(
    settings: Settings,
    worker_id: str,
    org_id: str,
    enabled_modules: list[str] | None = None,
    model_routes: dict[str, Any] | None = None,
    run_routes: dict[str, Any] | None = None,
    autonomy_policy: dict[str, Any] | None = None,
    paused: bool | None = None,
    max_run_minutes: int | None = None,
    max_total_run_minutes: int | None = None,
    max_item_attempts: int | None = None,
    claude_billing: str | None = None,
    enabled_kinds: list[str] | None = None,
    model_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create/patch a runner's config; only provided fields change. Returns the
    resolved config to push over the socket. US-31.2: `max_run_minutes` uses
    -1 as the explicit clear-to-default sentinel (None = leave unchanged)."""
    mr = json.dumps(model_routes) if model_routes is not None else None
    rr = json.dumps(run_routes) if run_routes is not None else None
    ap = json.dumps(autonomy_policy) if autonomy_policy is not None else None
    # US-53.4: None = leave unchanged; a list (empty included) stores as-is.
    ek = json.dumps(enabled_kinds) if enabled_kinds is not None else None
    # US-66.1: same "None = leave unchanged, {} stores as-is" convention.
    mo = json.dumps(model_overrides) if model_overrides is not None else None
    with _connect(settings) as conn:
        row = conn.execute(
            """
            insert into public.runner_config
              (worker_id, org_id, enabled_modules, model_routes, run_routes,
               autonomy_policy, paused, max_run_minutes, max_total_run_minutes,
               max_item_attempts, claude_billing, enabled_kinds, model_overrides)
            values (
              %s, %s,
              coalesce(%s::text[], '{}'::text[]),
              coalesce(%s::jsonb, '{}'::jsonb),
              coalesce(%s::jsonb, '{}'::jsonb),
              coalesce(%s::jsonb, '{}'::jsonb),
              coalesce(%s, false),
              %s,
              %s,
              coalesce(%s, 3),
              coalesce(%s, 'api'),
              %s::jsonb,
              coalesce(%s::jsonb, '{}'::jsonb)
            )
            on conflict (worker_id) do update set
              enabled_modules = coalesce(%s::text[], public.runner_config.enabled_modules),
              model_routes    = coalesce(%s::jsonb, public.runner_config.model_routes),
              run_routes      = coalesce(%s::jsonb, public.runner_config.run_routes),
              autonomy_policy = coalesce(%s::jsonb, public.runner_config.autonomy_policy),
              paused          = coalesce(%s, public.runner_config.paused),
              -- US-31.2: -1 is the explicit "clear it" sentinel — coalesce
              -- alone could never take a set lease back to the default.
              max_run_minutes = case
                when %s::int is null then public.runner_config.max_run_minutes
                when %s::int = -1 then null
                else %s::int end,
              -- US-39.2: same sentinel, same reason.
              max_total_run_minutes = case
                when %s::int is null then public.runner_config.max_total_run_minutes
                when %s::int = -1 then null
                else %s::int end,
              max_item_attempts = coalesce(%s, public.runner_config.max_item_attempts),
              claude_billing  = coalesce(%s, public.runner_config.claude_billing),
              enabled_kinds   = coalesce(%s::jsonb, public.runner_config.enabled_kinds),
              model_overrides = coalesce(%s::jsonb, public.runner_config.model_overrides),
              updated_at = now()
            returning enabled_modules, model_routes, run_routes, autonomy_policy,
                      paused, max_run_minutes, max_total_run_minutes,
                      max_item_attempts, claude_billing, enabled_kinds,
                      model_overrides
            """,
            (
                worker_id, org_id,
                enabled_modules, mr, rr, ap, paused,
                None if max_run_minutes in (None, -1) else max_run_minutes,
                None
                if max_total_run_minutes in (None, -1)
                else max_total_run_minutes,
                max_item_attempts,
                claude_billing,
                ek,
                mo,
                enabled_modules, mr, rr, ap, paused,
                max_run_minutes, max_run_minutes, max_run_minutes,
                max_total_run_minutes, max_total_run_minutes, max_total_run_minutes,
                max_item_attempts,
                claude_billing,
                ek,
                mo,
            ),
        ).fetchone()
        conn.commit()
    return {
        "enabled_modules": list(row["enabled_modules"]),
        "model_routes": row["model_routes"],
        "run_routes": row["run_routes"],
        "autonomy_policy": row["autonomy_policy"],
        "paused": bool(row["paused"]),
        "max_run_minutes": row["max_run_minutes"],
        "max_item_attempts": row["max_item_attempts"],
        # US-53.1: rides the config push so the supervisor reads it directly.
        "claude_billing": row["claude_billing"],
        # US-53.4: same ride; null = all kinds.
        "enabled_kinds": row["enabled_kinds"],
        # US-66.1: same ride.
        "model_overrides": row["model_overrides"] or {},
    }


# --------------------------------------------------------------------------
# LLM gateway scoped keys (US-10.3)
# --------------------------------------------------------------------------


def mint_gateway_key(
    settings: Settings,
    org_id: str,
    worker_id: str,
    run_id: str | None = None,
    route: str = "runner_brain",
    ttl_seconds: int = 3600,
    model: str | None = None,
    platform_billed: bool = False,
    session_id: str | None = None,
) -> str:
    """Mint a short-lived scoped key bound to {org, worker, run, route}. The
    plaintext is returned ONCE; only its hash is stored.

    US-27.8: `model` is the model the runner was configured with for this run
    kind. The gateway resolves the provider from it, because `route` is
    `runner_code` / `runner_plan` — keys that have no entry in LLM_FUNCTIONS
    and therefore route nowhere.

    US-60.1: `platform_billed` tells the gateway to resolve the platform's
    own Anthropic key instead of the org's configured provider — usage still
    records `org_id`/`worker_id` (the real customer), only credential
    resolution differs.

    US-83.2: `session_id` scopes a CLI-window session's key the way `run_id`
    scopes a run's, so a session's calls meter under the session instead of
    landing with both ids null."""
    key = "sfg_" + secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    with _connect(settings) as conn:
        conn.execute(
            """
            insert into public.llm_gateway_keys
              (key_hash, org_id, worker_id, run_id, session_id, route, model,
               platform_billed, expires_at)
            values (%s, %s, %s, %s, %s, %s, %s, %s, now() + make_interval(secs => %s))
            """,
            (
                key_hash,
                org_id,
                worker_id,
                run_id,
                session_id,
                route,
                (model or "").strip() or None,
                platform_billed,
                ttl_seconds,
            ),
        )
        conn.commit()
    return key


def validate_gateway_key(settings: Settings, key: str) -> dict[str, Any] | None:
    """Resolve a scoped key to its {org, worker, run, session, route}, or None
    if it's unknown, revoked, or expired."""
    if not key:
        return None
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    with _connect(settings) as conn:
        return conn.execute(
            """
            select k.org_id, k.worker_id, k.run_id, k.session_id, k.route,
                   k.model, k.platform_billed,
                   -- US-33.1: the project the spend belongs to. Joined here
                   -- rather than looked up per call in the gateway, and rather
                   -- than denormalised onto the key, which would go stale.
                   -- US-83.2: a session key attributes spend through the
                   -- session's own project the same way.
                   coalesce(r.project_id, s.project_id) as project_id
            from public.llm_gateway_keys k
            left join public.runs r on r.id = k.run_id
            left join public.agent_sessions s on s.id = k.session_id
            where k.key_hash = %s and k.revoked_at is null
              and k.expires_at > now()
            """,
            (key_hash,),
        ).fetchone()


def get_platform_llm_key(settings: Settings) -> dict[str, Any] | None:
    """US-60.1: the platform's own Anthropic key, resolved from Vault — the
    credential Buildmill Agent runs use instead of the org's own configured
    provider. None until the superadmin has set one."""
    with _connect(settings) as conn:
        row = conn.execute(
            "select model, vault_secret_id from public.platform_llm_key where id = true"
        ).fetchone()
    if not row or not row["vault_secret_id"]:
        return None
    from . import llm

    key = llm.read_vault_secret(settings, str(row["vault_secret_id"]))
    if not key:
        return None
    return {"model": row["model"], "key": key}


# --------------------------------------------------------------------------
# MCP server catalog, scoped keys and tool-call audit (US-34.1/34.2/34.4)
# --------------------------------------------------------------------------

_MCP_PUBLIC_COLUMNS = (
    "id, org_id, name, slug, description, transport, endpoint, command, "
    "declared_tools, needs_credential, credential_header, key_last4, enabled, "
    "last_checked_at, last_check_ok, last_check_error"
)
# Deliberately NOT `vault_secret_id`, and never the secret: the catalog is
# readable by the org and at most a last-four ever comes back out (us-34.1).


def list_mcp_servers(settings: Settings, org_id: str) -> list[dict[str, Any]]:
    with _connect(settings) as conn:
        return conn.execute(
            f"select {_MCP_PUBLIC_COLUMNS} from public.mcp_servers "
            "where org_id = %s order by name",
            (org_id,),
        ).fetchall()


def get_mcp_server(settings: Settings, server_id: str) -> dict[str, Any] | None:
    if not _valid_uuid(server_id):
        return None
    with _connect(settings) as conn:
        return conn.execute(
            f"select {_MCP_PUBLIC_COLUMNS} from public.mcp_servers where id = %s",
            (server_id,),
        ).fetchone()


def upsert_mcp_server(
    settings: Settings,
    org_id: str,
    entry: dict[str, Any],
    server_id: str | None = None,
) -> dict[str, Any]:
    with _connect(settings) as conn:
        if server_id and _valid_uuid(server_id):
            row = conn.execute(
                f"""
                update public.mcp_servers set
                  name = %s, slug = %s, description = %s, transport = %s,
                  endpoint = %s, command = %s, declared_tools = %s,
                  needs_credential = %s, credential_header = %s
                where id = %s and org_id = %s
                returning {_MCP_PUBLIC_COLUMNS}
                """,
                (
                    entry["name"], entry["slug"], entry["description"],
                    entry["transport"], entry["endpoint"], entry["command"],
                    entry["declared_tools"], entry["needs_credential"],
                    entry["credential_header"], server_id, org_id,
                ),
            ).fetchone()
        else:
            row = conn.execute(
                f"""
                insert into public.mcp_servers
                  (org_id, name, slug, description, transport, endpoint, command,
                   declared_tools, needs_credential, credential_header)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                returning {_MCP_PUBLIC_COLUMNS}
                """,
                (
                    org_id, entry["name"], entry["slug"], entry["description"],
                    entry["transport"], entry["endpoint"], entry["command"],
                    entry["declared_tools"], entry["needs_credential"],
                    entry["credential_header"],
                ),
            ).fetchone()
        conn.commit()
    return row


def set_mcp_server_enabled(
    settings: Settings, server_id: str, enabled: bool
) -> bool:
    if not _valid_uuid(server_id):
        return False
    with _connect(settings) as conn:
        row = conn.execute(
            "update public.mcp_servers set enabled = %s where id = %s returning id",
            (enabled, server_id),
        ).fetchone()
        conn.commit()
    return row is not None


def delete_mcp_server(settings: Settings, server_id: str) -> bool:
    """Removed outright, not archived: a preset naming a gone entry is flagged as
    unavailable (us-34.3) rather than the row lingering as a tool nobody can see."""
    if not _valid_uuid(server_id):
        return False
    with _connect(settings) as conn:
        row = conn.execute(
            "delete from public.mcp_servers where id = %s returning id",
            (server_id,),
        ).fetchone()
        conn.commit()
    return row is not None


def record_mcp_check(
    settings: Settings, server_id: str, ok: bool, error: str | None
) -> None:
    """us-27.13's rule: the result of the check lives where the value was entered."""
    if not _valid_uuid(server_id):
        return
    with _connect(settings) as conn:
        conn.execute(
            "update public.mcp_servers set last_checked_at = now(), "
            "last_check_ok = %s, last_check_error = %s where id = %s",
            (ok, (error or None) and error[:600], server_id),
        )
        conn.commit()


# The credential is written by the BROWSER, through the membership-gated
# `set_mcp_server_key` RPC — the same path `set_llm_provider_key` has always
# used. The API deliberately has no write helper here: a secret that never
# enters an API request body cannot appear in an API log, a traceback or a
# request trace, which is a smaller surface than one the API merely promises not
# to print. The API's one privileged operation is the read below.


def read_mcp_server_credential(
    settings: Settings, server_id: str
) -> str | None:
    """The proxy's one privileged read. Nothing else calls this, and its result
    never enters a response, a log, a trace or an audit row (us-34.2)."""
    if not _valid_uuid(server_id):
        return None
    with _connect(settings) as conn:
        row = conn.execute(
            """
            select s.decrypted_secret as secret
            from public.mcp_servers m
            join vault.decrypted_secrets s on s.id = m.vault_secret_id
            where m.id = %s
            """,
            (server_id,),
        ).fetchone()
    return row["secret"] if row else None


# ------------------------------------------------------ US-34.2: scoped keys


def mint_mcp_key(
    settings: Settings,
    org_id: str,
    worker_id: str,
    run_id: str,
    ttl_minutes: int = 240,
) -> str:
    """A key for ONE run, dead when the run ends.

    Scoped exactly the way a gateway key is (us-10.3): a leaked key is worth one
    finished run. That property is what makes proxying third-party credentials
    safe at all, so it is not configurable.
    """
    key = f"sfm_{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    with _connect(settings) as conn:
        conn.execute(
            """
            insert into public.mcp_scoped_keys
              (key_hash, org_id, worker_id, run_id, expires_at)
            values (%s, %s, %s, %s, now() + make_interval(mins => %s))
            """,
            (key_hash, org_id, worker_id, run_id, ttl_minutes),
        )
        conn.commit()
    return key


def validate_mcp_key(settings: Settings, key: str) -> dict[str, Any] | None:
    """Resolve a scoped key to its run — and refuse it once that run has ended.

    The run's status is checked here rather than trusted from `expires_at`: a run
    that finished in two minutes should not leave a four-hour key alive.
    """
    if not key:
        return None
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    with _connect(settings) as conn:
        row = conn.execute(
            """
            select k.org_id, k.worker_id, k.run_id, r.status, r.project_id,
                   r.tool_surface
            from public.mcp_scoped_keys k
            left join public.runs r on r.id = k.run_id
            where k.key_hash = %s and k.revoked_at is null
              and k.expires_at > now()
            """,
            (key_hash,),
        ).fetchone()
    if not row or row["status"] != "running":
        return None
    return row


def revoke_mcp_keys_for_run(settings: Settings, run_id: str) -> None:
    if not _valid_uuid(run_id):
        return
    with _connect(settings) as conn:
        conn.execute(
            "update public.mcp_scoped_keys set revoked_at = now() "
            "where run_id = %s and revoked_at is null",
            (run_id,),
        )
        conn.commit()


# ---------------------------------------------- US-34.3: the recorded surface


def record_tool_surface(settings: Settings, run_id: str, surface: dict[str, Any]) -> None:
    if not _valid_uuid(run_id):
        return
    with _connect(settings) as conn:
        conn.execute(
            "update public.runs set tool_surface = %s::jsonb where id = %s",
            (json.dumps(surface), run_id),
        )
        conn.commit()


def project_withheld_servers(settings: Settings, project_id: str | None) -> list[str]:
    if not project_id or not _valid_uuid(project_id):
        return []
    with _connect(settings) as conn:
        row = conn.execute(
            "select mcp_withheld from public.projects where id = %s",
            (project_id,),
        ).fetchone()
    return [str(x) for x in ((row or {}).get("mcp_withheld") or [])]


# ------------------------------------------------- US-34.4: the tool-call audit


def record_tool_call(settings: Settings, call: dict[str, Any]) -> bool:
    """One record per proxied call. Returns False if it could not be written.

    The caller counts a False rather than raising: the proxy's job is to relay,
    and an audit that fails the call it is auditing is worse than the gap.
    """
    try:
        with _connect(settings) as conn:
            conn.execute(
                """
                insert into public.mcp_tool_calls
                  (org_id, run_id, worker_id, server_id, server_name, tool,
                   arguments_redacted, outcome, error, duration_ms, response_bytes)
                values (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
                """,
                (
                    call["org_id"],
                    call.get("run_id"),
                    call.get("worker_id"),
                    call.get("server_id"),
                    (call.get("server_name") or "")[:80],
                    (call.get("tool") or "")[:120],
                    json.dumps(call.get("arguments_redacted")),
                    call.get("outcome") or "ok",
                    (call.get("error") or None) and str(call["error"])[:600],
                    call.get("duration_ms"),
                    call.get("response_bytes"),
                ),
            )
            conn.commit()
        return True
    except Exception:  # noqa: BLE001 — the gap is counted by the caller
        logger.warning("tool-call audit write failed", exc_info=True)
        return False


def count_dropped_tool_call(settings: Settings, run_id: str | None) -> None:
    """A record that could not be written must not vanish silently: a lossy audit
    is worse than none, because it is believed."""
    if not run_id or not _valid_uuid(str(run_id)):
        return
    try:
        with _connect(settings) as conn:
            conn.execute("select public.count_dropped_tool_call(%s)", (run_id,))
            conn.commit()
    except Exception:  # noqa: BLE001
        logger.warning("could not count a dropped tool-call record", exc_info=True)


# --------------------------------------------------------------------------
# LLM usage metering (US-33.1)
# --------------------------------------------------------------------------


def model_prices(settings: Settings, org_id: str) -> dict[str, dict[str, float]]:
    """The org's per-model rates, per million tokens."""
    with _connect(settings) as conn:
        rows = conn.execute(
            "select model, input_per_mtok, output_per_mtok, "
            "cache_read_per_mtok, cache_write_per_mtok "
            "from public.llm_model_prices where org_id = %s",
            (org_id,),
        ).fetchall()
    return {
        r["model"]: {
            "input_per_mtok": float(r["input_per_mtok"]),
            "output_per_mtok": float(r["output_per_mtok"]),
            # US-38.1: None, not a default. An unset cache rate charges the
            # input rate, which cost_for does -- filling in a guess here would
            # bury that decision one layer down.
            "cache_read_per_mtok": (
                float(r["cache_read_per_mtok"])
                if r["cache_read_per_mtok"] is not None
                else None
            ),
            "cache_write_per_mtok": (
                float(r["cache_write_per_mtok"])
                if r["cache_write_per_mtok"] is not None
                else None
            ),
        }
        for r in rows
    }


def set_model_price(
    settings: Settings,
    org_id: str,
    model: str,
    input_per_mtok: float,
    output_per_mtok: float,
    cache_read_per_mtok: float | None = None,
    cache_write_per_mtok: float | None = None,
) -> dict[str, Any]:
    with _connect(settings) as conn:
        row = conn.execute(
            """
            insert into public.llm_model_prices
              (org_id, model, input_per_mtok, output_per_mtok,
               cache_read_per_mtok, cache_write_per_mtok)
            values (%s, %s, %s, %s, %s, %s)
            on conflict (org_id, model) do update set
              input_per_mtok = excluded.input_per_mtok,
              output_per_mtok = excluded.output_per_mtok,
              cache_read_per_mtok = excluded.cache_read_per_mtok,
              cache_write_per_mtok = excluded.cache_write_per_mtok
            returning model, input_per_mtok, output_per_mtok,
                      cache_read_per_mtok, cache_write_per_mtok
            """,
            (
                org_id,
                model,
                input_per_mtok,
                output_per_mtok,
                cache_read_per_mtok,
                cache_write_per_mtok,
            ),
        ).fetchone()
        conn.commit()
    return row


def delete_model_price(settings: Settings, org_id: str, model: str) -> bool:
    with _connect(settings) as conn:
        row = conn.execute(
            "delete from public.llm_model_prices where org_id = %s and model = %s "
            "returning model",
            (org_id, model),
        ).fetchone()
        conn.commit()
    return row is not None


def record_llm_usage(settings: Settings, usage: dict[str, Any]) -> None:
    """One append-only row for one model call, and the per-run rollup.

    US-33.1: called from the gateway's relay teardown. Every caller wraps it,
    because metering is the secondary duty and loses every conflict — but this
    also guards the ids itself, so a malformed claim cannot raise here.
    """
    run_id = usage.get("run_id")
    # US-83.2: a CLI-window session's calls carry its session id — the column
    # existed from Phase 78 with no writer, so they landed with both ids null.
    session_id = usage.get("session_id")
    worker_id = usage.get("worker_id")
    project_id = usage.get("project_id")
    provider_id = usage.get("provider_id")
    with _connect(settings) as conn:
        conn.execute(
            """
            insert into public.llm_usage
              (org_id, run_id, session_id, worker_id, project_id, provider_id,
               provider_type, provider_name, model, route,
               tokens_in, tokens_out, cache_read_tokens, cache_write_tokens,
               parsed, parse_note,
               rate_in_per_mtok, rate_out_per_mtok, cost_usd, status_code,
               latency_ms)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s)
            """,
            (
                usage["org_id"],
                run_id if run_id and _valid_uuid(str(run_id)) else None,
                session_id if session_id and _valid_uuid(str(session_id)) else None,
                worker_id if worker_id and _valid_uuid(str(worker_id)) else None,
                project_id if project_id and _valid_uuid(str(project_id)) else None,
                provider_id if provider_id and _valid_uuid(str(provider_id)) else None,
                (usage.get("provider_type") or "")[:40],
                (usage.get("provider_name") or "")[:80],
                (usage.get("model") or "")[:200],
                (usage.get("route") or "")[:80],
                usage.get("tokens_in"),
                usage.get("tokens_out"),
                # US-38.1: nullable on purpose -- NULL is "the provider said
                # nothing", which is not the same as "nothing was cached".
                usage.get("cache_read_tokens"),
                usage.get("cache_write_tokens"),
                bool(usage.get("parsed")),
                (usage.get("parse_note") or None),
                usage.get("rate_in_per_mtok"),
                usage.get("rate_out_per_mtok"),
                usage.get("cost_usd"),
                usage.get("status_code"),
                # US-62.3: wall-clock start-of-call to end-of-stream, in ms —
                # never null for a call that reached this insert at all.
                usage.get("latency_ms"),
            ),
        )
        # The dead columns on `runs` finally mean something. Recomputed rather
        # than incremented, so a late row cannot leave a stale total.
        if run_id and _valid_uuid(str(run_id)):
            conn.execute("select public.rollup_run_usage(%s)", (run_id,))
        conn.commit()


def set_manager_settings_override(
    settings: Settings, run_id: str, preset_id: str
) -> bool:
    """US-33.5: stamp the manager's dispatch-time choice into the run's context.

    The resolver (us-32.7) reads `input_context.settings_override.manager` at
    claim and puts it at the top of precedence, so this needs no new plumbing —
    and the run detail already labels each value's source, which is what makes
    the choice explainable afterwards.

    The preset's VALUES are copied, not referenced: editing the preset later must
    not silently change what the manager asked for on this run.
    """
    if not (_valid_uuid(run_id) and _valid_uuid(preset_id)):
        return False
    with _connect(settings) as conn:
        preset = conn.execute(
            "select p.id, p.name, p.model, p.settings, p.version "
            "from public.agent_presets p "
            "join public.runs r on r.org_id = p.org_id "
            "where p.id = %s and r.id = %s and p.archived_at is null",
            (preset_id, run_id),
        ).fetchone()
        if not preset:
            return False
        values = dict(preset["settings"] or {})
        if preset["model"]:
            values["model"] = preset["model"]
        row = conn.execute(
            """
            update public.runs
            set input_context = coalesce(input_context, '{}'::jsonb)
              || jsonb_build_object(
                   'settings_override',
                   coalesce(input_context->'settings_override', '{}'::jsonb)
                     || jsonb_build_object('manager', %s::jsonb,
                                           'manager_preset', %s::jsonb)
                 )
            where id = %s and status = 'queued'
            returning id
            """,
            (
                json.dumps(values),
                json.dumps(
                    {
                        "id": str(preset["id"]),
                        "name": preset["name"],
                        "version": preset["version"],
                    }
                ),
                run_id,
            ),
        ).fetchone()
        conn.commit()
    return row is not None


def escalation_for(
    settings: Settings, org_id: str, issue_id: str | None, kind: str
) -> tuple[dict[str, Any] | None, str | None]:
    """US-33.4: the supervisor's override for a retry, and the reason for it.

    Returns (override, reason) or (None, None). The override is the next preset
    up the ladder from whatever the last attempt actually ran — read off that
    run's own record, so a preset edited since cannot change what escalating
    means for this item.

    Escalates only on `work-fault`. A transient network error escalates nothing:
    retrying at higher effort would be superstition, and expensive superstition.
    us-10.11's classifier is what decides, and a class it could not identify does
    not escalate.
    """
    if not issue_id or not _valid_uuid(issue_id):
        return None, None
    with _connect(settings) as conn:
        last = conn.execute(
            """
            select r.id, r.status, r.fault_class, r.preset_id, r.preset_name
            from public.runs r
            where r.issue_id = %s and r.kind = %s
              and r.status in ('failed', 'stopped')
            order by r.finished_at desc nulls last, r.created_at desc
            limit 1
            """,
            (issue_id, kind),
        ).fetchone()
        if not last or not last["preset_id"]:
            return None, None
        if last["fault_class"] != "work-fault":
            # A broken box is not answered by thinking harder.
            return None, None
        nxt = conn.execute(
            """
            select up.id, up.name, up.model, up.settings, up.version
            from public.agent_presets p
            join public.agent_presets up on up.id = p.escalates_to
            where p.id = %s and p.org_id = %s and up.archived_at is null
            """,
            (str(last["preset_id"]), org_id),
        ).fetchone()
    if not nxt:
        # The ladder ends. No run climbs forever.
        return None, None
    values = dict(nxt["settings"] or {})
    if nxt["model"]:
        values["model"] = nxt["model"]
    reason = (
        f"escalated from '{last['preset_name'] or 'the previous preset'}' to "
        f"'{nxt['name']}' (v{nxt['version']}): the last attempt on this item was "
        "classified as a work-fault, which more capability can plausibly answer. "
        "This does not buy extra attempts — it changes how the remaining ones "
        "are spent."
    )
    return values, reason


SPEND_DIMENSIONS = {
    # US-33.3: four dimensions, ONE grain — filterable and groupable over a time
    # range, not four separate reports. Keyed on worker ID, never name: us-32.2
    # made names editable and deliberately non-unique.
    "project": "u.project_id::text",
    "agent": "u.worker_id::text",
    "provider": "coalesce(nullif(u.provider_name, ''), u.provider_type)",
    "model": "u.model",
    # US-60.2: the superadmin's cross-org view only — an org's own Spend page
    # never groups by org, it already knows which one it is.
    "org": "u.org_id::text",
    # us-95.3: the work-shaped axes, resolved by walking the usage row's run
    # to its work item at read time. Rows that cannot be walked — no run, or
    # a batch run whose ledger does not say which item a call served — keep a
    # NULL key and land in ONE named bucket, never dropped and never
    # pro-rated: a guessed attribution is worse than a named gap.
    "type": "i.type",
    "epic": "i.epic_id::text",
    "item": "i.id::text",
}

# The dimensions (and the item_type filter) that need the run -> issue walk.
# LEFT joins, so unattributable usage keeps its row; skipped entirely for the
# infrastructure dimensions, which have answered without them since US-33.3.
ISSUE_SPEND_DIMENSIONS = frozenset({"type", "epic", "item"})
_ISSUE_SPEND_JOIN = (
    " left join public.runs r on r.id = u.run_id"
    " left join public.issues i on i.id = r.issue_id"
)

ISSUE_TYPES = ("feature", "bug", "chore", "story")

# The one row work-shaped groupings may not silently omit (us-95.3 AC3).
UNATTRIBUTABLE_LABEL = "Not attributable to a work item"


def spend_breakdown(
    settings: Settings,
    org_id: str | None,
    *,
    group_by: str = "project",
    days: int = 30,
    project_id: str | None = None,
    worker_id: str | None = None,
    item_type: str | None = None,
) -> dict[str, Any]:
    """US-33.3: tokens in, tokens out and cost, grouped one way, over a window.

    Every figure is computed from the append-only usage rows at read time. No
    aggregate counter column exists, because a maintained counter can drift from
    the events it summarises — and a drifted cost figure is worse than no cost
    figure, because it will be believed.

    Cost sums the per-row `cost_usd`, each computed from the rate that was in
    force when the call was made. A model repriced today therefore does not
    rewrite last month.

    US-60.2: `org_id=None` means no org filter at all — every org at once.
    Only the platform-admin-gated `/admin/usage` route may call it that way;
    the org-scoped `/llm/org-spend` route always supplies its own org id.

    us-95.3/95.4: `type`/`epic`/`item` group through the run -> issue walk;
    `item_type` narrows to one work-item type through the same walk (which
    inherently excludes the unattributable rows — a bug filter cannot vouch
    for money it cannot attribute).
    """
    if group_by not in SPEND_DIMENSIONS:
        group_by = "project"
    try:
        window = max(1, min(int(days), 366))
    except (TypeError, ValueError):
        window = 30
    if item_type not in ISSUE_TYPES:
        item_type = None
    expr = SPEND_DIMENSIONS[group_by]
    join = (
        _ISSUE_SPEND_JOIN
        if group_by in ISSUE_SPEND_DIMENSIONS or item_type
        else ""
    )
    clauses = [f"u.created_at > now() - interval '{window} days'"]
    params: list[Any] = []
    if org_id and _valid_uuid(org_id):
        clauses.append("u.org_id = %s")
        params.append(org_id)
    if project_id and _valid_uuid(project_id):
        clauses.append("u.project_id = %s")
        params.append(project_id)
    if worker_id and _valid_uuid(worker_id):
        clauses.append("u.worker_id = %s")
        params.append(worker_id)
    if item_type:
        clauses.append("i.type = %s")
        params.append(item_type)
    where = " and ".join(clauses)
    with _connect(settings) as conn:
        rows = conn.execute(
            f"""
            select {expr} as key,
                   coalesce(sum(u.tokens_in), 0)  as tokens_in,
                   coalesce(sum(u.tokens_out), 0) as tokens_out,
                   -- US-38.1: the cache share of the input, so a manager can
                   -- see whether caching is working at all. Summed over rows
                   -- that report it; rows predating the split carry NULL and
                   -- contribute nothing rather than a false zero.
                   coalesce(sum(u.cache_read_tokens), 0)  as cache_read_tokens,
                   coalesce(sum(u.cache_write_tokens), 0) as cache_write_tokens,
                   nullif(coalesce(sum(u.cost_usd), 0), 0) as cost_usd,
                   count(*) as calls,
                   -- US-33.3: what we could NOT measure, alongside the totals.
                   -- A breakdown that silently omits it claims a completeness
                   -- it does not have.
                   count(*) filter (where not u.parsed) as unparsed_calls
            from public.llm_usage u{join}
            where {where}
            group by 1
            -- us-95.3: by cost. (`4` had pointed at cost until US-38.1's two
            -- cache columns shifted it onto cache reads — position 6 is cost
            -- again, and the tie-break stays tokens in.)
            order by 6 desc nulls last, 2 desc
            limit 200
            """,
            tuple(params),
        ).fetchall()
        # Labels for the id-keyed dimensions, resolved here so the caller never
        # has to join by hand.
        labels: dict[str, str] = {}
        keys = [r["key"] for r in rows if r["key"]]
        if keys and group_by == "project":
            for row in conn.execute(
                "select id::text as id, name from public.projects "
                "where id = any(%s::uuid[])",
                (keys,),
            ).fetchall():
                labels[row["id"]] = row["name"]
        elif keys and group_by == "agent":
            for row in conn.execute(
                """
                select w.id::text as id,
                       coalesce(p.display_name, w.name, 'an agent') as name
                from public.workers w
                left join public.principals p on p.id = w.principal_id
                where w.id = any(%s::uuid[])
                """,
                (keys,),
            ).fetchall():
                labels[row["id"]] = row["name"]
        elif keys and group_by == "org":
            for row in conn.execute(
                "select id::text as id, name from public.organizations "
                "where id = any(%s::uuid[])",
                (keys,),
            ).fetchall():
                labels[row["id"]] = row["name"]
        elif keys and group_by == "type":
            # Constraint values read as labels with a capital; no lookup table
            # exists to disagree with.
            labels = {k: k.capitalize() for k in keys}
        elif keys and group_by == "epic":
            # us-95.3 AC2: epic `number` repeats across projects — a bare "E4"
            # from two projects would collapse two epics into one label, so the
            # project's name always rides along.
            for row in conn.execute(
                """
                select e.id::text as id, e.number, e.title, p.name as project
                from public.epics e
                join public.projects p on p.id = e.project_id
                where e.id = any(%s::uuid[])
                """,
                (keys,),
            ).fetchall():
                labels[row["id"]] = (
                    f"E{row['number']} — {row['title']} · {row['project']}"
                )
        elif keys and group_by == "item":
            # Same display-id composition the issue surfaces use
            # (US-<epic>.<item>[.<sub>]); an item never numbered falls back to
            # its title alone rather than a malformed id.
            for row in conn.execute(
                """
                select i.id::text as id, i.title,
                       case when e.number is not null and i.item_no is not null
                         then 'US-' || e.number || '.' || i.item_no ||
                              coalesce('.' || i.sub_no, '')
                       end as display_id
                from public.issues i
                left join public.epics e on e.id = i.epic_id
                where i.id = any(%s::uuid[])
                """,
                (keys,),
            ).fetchall():
                labels[row["id"]] = (
                    f"{row['display_id']} — {row['title']}"
                    if row["display_id"]
                    else row["title"]
                )
    null_label = (
        UNATTRIBUTABLE_LABEL
        if group_by in ISSUE_SPEND_DIMENSIONS
        else "unattributed"
    )
    out = []
    for r in rows:
        key = r["key"]
        out.append(
            {
                "key": key,
                "label": labels.get(key or "", key or null_label),
                "tokens_in": int(r["tokens_in"]),
                "tokens_out": int(r["tokens_out"]),
                "cache_read_tokens": int(r["cache_read_tokens"]),
                "cache_write_tokens": int(r["cache_write_tokens"]),
                "cost_usd": float(r["cost_usd"]) if r["cost_usd"] is not None else None,
                "calls": int(r["calls"]),
                "unparsed_calls": int(r["unparsed_calls"]),
            }
        )
    return {
        "group_by": group_by,
        "days": window,
        "rows": out,
        "totals": {
            "tokens_in": sum(r["tokens_in"] for r in out),
            "tokens_out": sum(r["tokens_out"] for r in out),
            "cache_read_tokens": sum(r["cache_read_tokens"] for r in out),
            "cache_write_tokens": sum(r["cache_write_tokens"] for r in out),
            "cost_usd": (
                round(sum(r["cost_usd"] or 0 for r in out), 6)
                if any(r["cost_usd"] is not None for r in out)
                else None
            ),
            "calls": sum(r["calls"] for r in out),
            "unparsed_calls": sum(r["unparsed_calls"] for r in out),
        },
    }


def spend_trend(
    settings: Settings,
    org_id: str,
    *,
    days: int = 30,
    project_id: str | None = None,
    worker_id: str | None = None,
    item_type: str | None = None,
) -> dict[str, Any]:
    """us-95.2: the window as a daily curve, and the window set against the
    window before it.

    Day buckets are UTC calendar dates over the same `created_at > now() - N
    days` predicate the breakdown uses, so the series sums to exactly the
    total the table shows — one source of dollars (us-91.11 AC4). Days with
    no metered calls are real zeros, filled here rather than left for the
    client to infer from gaps. The previous window is a single total over
    (now-2N, now-N] with the same filters — enough to say "up 40%", no more.

    us-95.4: accepts the same three filters as the breakdown, so the curve
    and the table can never be governed by different controls.
    """
    try:
        window = max(1, min(int(days), 366))
    except (TypeError, ValueError):
        window = 30
    if item_type not in ISSUE_TYPES:
        item_type = None
    join = _ISSUE_SPEND_JOIN if item_type else ""
    clauses = [f"u.created_at > now() - interval '{2 * window} days'"]
    params: list[Any] = []
    if org_id and _valid_uuid(org_id):
        clauses.append("u.org_id = %s")
        params.append(org_id)
    if project_id and _valid_uuid(project_id):
        clauses.append("u.project_id = %s")
        params.append(project_id)
    if worker_id and _valid_uuid(worker_id):
        clauses.append("u.worker_id = %s")
        params.append(worker_id)
    if item_type:
        clauses.append("i.type = %s")
        params.append(item_type)
    where = " and ".join(clauses)
    with _connect(settings) as conn:
        rows = conn.execute(
            f"""
            select (u.created_at at time zone 'utc')::date as day,
                   u.created_at > now() - interval '{window} days' as in_window,
                   coalesce(sum(u.cost_usd), 0) as cost_usd,
                   coalesce(sum(u.tokens_in), 0)  as tokens_in,
                   coalesce(sum(u.tokens_out), 0) as tokens_out,
                   count(*) as calls,
                   count(*) filter (where not u.parsed) as unparsed_calls
            from public.llm_usage u{join}
            where {where}
            group by 1, 2
            """,
            tuple(params),
        ).fetchall()
    # The calendar day the cutoff falls on can straddle it; grouping by the
    # in-window flag as well keeps that day's out-of-window portion in the
    # previous total instead of inflating the series.
    by_day: dict[str, dict[str, Any]] = {}
    prev_cost = 0.0
    prev_calls = 0
    for r in rows:
        if r["in_window"]:
            by_day[r["day"].isoformat()] = r
        else:
            prev_cost += float(r["cost_usd"])
            prev_calls += int(r["calls"])
    today = dt.datetime.now(dt.timezone.utc).date()
    first = today - dt.timedelta(days=window)
    series = []
    for offset in range((today - first).days + 1):
        day = (first + dt.timedelta(days=offset)).isoformat()
        r = by_day.get(day)
        series.append(
            {
                "day": day,
                "cost_usd": float(r["cost_usd"]) if r else 0.0,
                "tokens_in": int(r["tokens_in"]) if r else 0,
                "tokens_out": int(r["tokens_out"]) if r else 0,
                "calls": int(r["calls"]) if r else 0,
                "unparsed_calls": int(r["unparsed_calls"]) if r else 0,
            }
        )
    total = round(sum(p["cost_usd"] for p in series), 6)
    any_calls = any(p["calls"] for p in series)
    return {
        "days": window,
        "series": series,
        # Same semantics as the breakdown totals: None means "nothing metered
        # carried a price", which must not read as free.
        "total_cost_usd": total if total else (0.0 if any_calls else None),
        "previous_cost_usd": round(prev_cost, 6) if prev_calls else None,
        "previous_calls": prev_calls,
        "calls": sum(p["calls"] for p in series),
        "unparsed_calls": sum(p["unparsed_calls"] for p in series),
    }


def preset_outcomes(
    settings: Settings, org_id: str, days: int = 90
) -> list[dict[str, Any]]:
    """US-33.6: how each preset version actually performed.

    Grouped by preset NAME AND VERSION, because us-32.5 versions presets exactly
    so this is answerable: "Deep got worse last week" needs the two versions to
    be separate rows, not one average that hides the change.

    Every figure comes from the runs themselves — the outcome they reached, the
    cost us-33.1 rolled up, the time they took. Custom (unpreset) runs are
    excluded rather than lumped together: a single-use hand-tuned row is not a
    preset and averaging it in would be the exact noise us-32.6 avoided by
    storing custom settings inline.
    """
    try:
        window = max(1, min(int(days), 366))
    except (TypeError, ValueError):
        window = 90
    with _connect(settings) as conn:
        rows = conn.execute(
            f"""
            select r.preset_name as name,
                   r.preset_version as version,
                   count(*) as runs,
                   count(*) filter (where r.status = 'succeeded') as succeeded,
                   count(*) filter (where r.status = 'failed') as failed,
                   count(*) filter (where r.status = 'stopped') as stopped,
                   nullif(coalesce(sum(r.cost_usd), 0), 0) as cost_usd,
                   avg(r.cost_usd) as avg_cost_usd,
                   avg(
                     extract(epoch from (r.finished_at - r.started_at))
                   ) filter (where r.finished_at is not null
                             and r.started_at is not null) as avg_seconds
            from public.runs r
            where r.org_id = %s
              and r.preset_name is not null
              and r.created_at > now() - interval '{window} days'
              and r.status in ('succeeded', 'failed', 'stopped')
            group by 1, 2
            order by 3 desc
            limit 100
            """,
            (org_id,),
        ).fetchall()
    out = []
    for r in rows:
        runs = int(r["runs"])
        out.append(
            {
                "name": r["name"],
                "version": r["version"],
                "runs": runs,
                "succeeded": int(r["succeeded"]),
                "failed": int(r["failed"]),
                "stopped": int(r["stopped"]),
                # A rate is what the comparison is actually about; the counts are
                # there so a 1-run "100%" cannot be mistaken for a result.
                "success_rate": round(int(r["succeeded"]) / runs, 3) if runs else None,
                "cost_usd": float(r["cost_usd"]) if r["cost_usd"] is not None else None,
                "avg_cost_usd": (
                    round(float(r["avg_cost_usd"]), 6)
                    if r["avg_cost_usd"] is not None
                    else None
                ),
                "avg_seconds": (
                    round(float(r["avg_seconds"])) if r["avg_seconds"] is not None else None
                ),
            }
        )
    return out


RUN_ANALYTICS_DIMENSIONS = {
    # US-62.1: the four ways a manager wants runs sliced when tuning timeouts —
    # by task type, by project, by org (superadmin cross-org view only), or by
    # the agent that ran them. Same keyed-on-id-never-name discipline
    # `SPEND_DIMENSIONS` already established, for the same reason (us-32.2).
    "kind": "r.kind",
    "project": "r.project_id::text",
    "org": "r.org_id::text",
    "agent": "r.worker_id::text",
}


def run_analytics(
    settings: Settings,
    org_id: str | None,
    *,
    group_by: str = "kind",
    days: int = 30,
    project_id: str | None = None,
    worker_id: str | None = None,
    kind: str | None = None,
) -> dict[str, Any]:
    """US-62.1: every run, sliced one way, with the duration spread a timeout
    decision actually needs — not just an average.

    Generalizes `preset_outcomes`'s shape (compute from `runs` at read time,
    `filter (where ...)` for status buckets, `extract(epoch from (finished_at -
    started_at))` for duration) across kind/project/org/agent instead of only
    preset name+version, and adds min/max/p95 alongside the average: an
    average that hides a 40-minute outlier is exactly what a manager sizing
    `max_run_minutes` cannot use. Cancelled runs are their own bucket, not
    excluded — a kind that gets manually cancelled a lot is a different signal
    from one that times out, and burying one inside "not counted" hides it.

    `org_id=None` means every org at once — only the platform-admin-gated
    route may call it that way, mirroring `spend_breakdown`'s us-60.2 rule.
    """
    if group_by not in RUN_ANALYTICS_DIMENSIONS:
        group_by = "kind"
    try:
        window = max(1, min(int(days), 366))
    except (TypeError, ValueError):
        window = 30
    expr = RUN_ANALYTICS_DIMENSIONS[group_by]
    clauses = [f"r.created_at > now() - interval '{window} days'"]
    params: list[Any] = []
    if org_id and _valid_uuid(org_id):
        clauses.append("r.org_id = %s")
        params.append(org_id)
    if project_id and _valid_uuid(project_id):
        clauses.append("r.project_id = %s")
        params.append(project_id)
    if worker_id and _valid_uuid(worker_id):
        clauses.append("r.worker_id = %s")
        params.append(worker_id)
    if kind:
        clauses.append("r.kind = %s")
        params.append(kind)
    where = " and ".join(clauses)
    duration = "extract(epoch from (r.finished_at - r.started_at))"
    finished = "r.finished_at is not null and r.started_at is not null"
    with _connect(settings) as conn:
        rows = conn.execute(
            f"""
            select {expr} as key,
                   count(*) as runs,
                   count(*) filter (where r.status = 'succeeded') as succeeded,
                   count(*) filter (where r.status = 'failed') as failed,
                   count(*) filter (where r.status = 'stopped') as stopped,
                   count(*) filter (where r.status = 'cancelled') as cancelled,
                   nullif(coalesce(sum(r.cost_usd), 0), 0) as cost_usd,
                   avg({duration}) filter (where {finished}) as avg_seconds,
                   min({duration}) filter (where {finished}) as min_seconds,
                   max({duration}) filter (where {finished}) as max_seconds,
                   percentile_cont(0.95) within group (order by {duration})
                     filter (where {finished}) as p95_seconds
            from public.runs r
            where {where}
            group by 1
            order by 2 desc
            limit 200
            """,
            tuple(params),
        ).fetchall()
        labels: dict[str, str] = {}
        keys = [r["key"] for r in rows if r["key"]]
        if keys and group_by == "project":
            for row in conn.execute(
                "select id::text as id, name from public.projects "
                "where id = any(%s::uuid[])",
                (keys,),
            ).fetchall():
                labels[row["id"]] = row["name"]
        elif keys and group_by == "agent":
            for row in conn.execute(
                """
                select w.id::text as id,
                       coalesce(p.display_name, w.name, 'an agent') as name
                from public.workers w
                left join public.principals p on p.id = w.principal_id
                where w.id = any(%s::uuid[])
                """,
                (keys,),
            ).fetchall():
                labels[row["id"]] = row["name"]
        elif keys and group_by == "org":
            for row in conn.execute(
                "select id::text as id, name from public.organizations "
                "where id = any(%s::uuid[])",
                (keys,),
            ).fetchall():
                labels[row["id"]] = row["name"]
        # US-62.2: attempts per agent — `run_attempts` is the only record of
        # how many times an agent actually tried an item (a lease requeue
        # mutates the run row in place, so `runs` alone undercounts). Only
        # meaningful grouped by agent; every other dimension leaves it null.
        attempts: dict[str, int] = {}
        if keys and group_by == "agent":
            attempt_clauses = [f"a.created_at > now() - interval '{window} days'"]
            attempt_params: list[Any] = []
            if org_id and _valid_uuid(org_id):
                attempt_clauses.append("a.org_id = %s")
                attempt_params.append(org_id)
            attempt_where = " and ".join(attempt_clauses)
            for row in conn.execute(
                f"""
                select a.worker_id::text as id, count(*) as attempts
                from public.run_attempts a
                where {attempt_where} and a.worker_id = any(%s::uuid[])
                group by 1
                """,
                (*attempt_params, keys),
            ).fetchall():
                attempts[row["id"]] = int(row["attempts"])
    out = []
    for r in rows:
        key = r["key"]
        runs_n = int(r["runs"])
        succeeded = int(r["succeeded"])
        attempts_n = attempts.get(key or "") if group_by == "agent" else None
        out.append(
            {
                "key": key,
                "label": labels.get(key or "", key or "unattributed"),
                "runs": runs_n,
                "succeeded": succeeded,
                "failed": int(r["failed"]),
                "stopped": int(r["stopped"]),
                "cancelled": int(r["cancelled"]),
                "success_rate": round(succeeded / runs_n, 3) if runs_n else None,
                "cost_usd": float(r["cost_usd"]) if r["cost_usd"] is not None else None,
                "avg_seconds": (
                    round(float(r["avg_seconds"])) if r["avg_seconds"] is not None else None
                ),
                "min_seconds": (
                    round(float(r["min_seconds"])) if r["min_seconds"] is not None else None
                ),
                "max_seconds": (
                    round(float(r["max_seconds"])) if r["max_seconds"] is not None else None
                ),
                "p95_seconds": (
                    round(float(r["p95_seconds"])) if r["p95_seconds"] is not None else None
                ),
                "attempts": attempts_n,
                "attempts_per_run": (
                    round(attempts_n / runs_n, 2) if attempts_n is not None and runs_n else None
                ),
            }
        )
    return {"group_by": group_by, "days": window, "rows": out}


def run_analytics_detail(
    settings: Settings,
    org_id: str | None,
    *,
    group_by: str,
    key: str,
    days: int = 30,
    project_id: str | None = None,
    worker_id: str | None = None,
    kind: str | None = None,
) -> list[dict[str, Any]]:
    """US-62.1: the individual runs behind one `run_analytics` row — a summary
    that hides a bad outlier is only half a report; this is the other half,
    one click away."""
    if group_by not in RUN_ANALYTICS_DIMENSIONS or not key:
        return []
    try:
        window = max(1, min(int(days), 366))
    except (TypeError, ValueError):
        window = 30
    key_clause = {
        "kind": "r.kind = %s",
        "project": "r.project_id = %s",
        "org": "r.org_id = %s",
        "agent": "r.worker_id = %s",
    }[group_by]
    clauses = [f"r.created_at > now() - interval '{window} days'", key_clause]
    params: list[Any] = [key]
    if org_id and _valid_uuid(org_id):
        clauses.append("r.org_id = %s")
        params.append(org_id)
    if project_id and _valid_uuid(project_id):
        clauses.append("r.project_id = %s")
        params.append(project_id)
    if worker_id and _valid_uuid(worker_id):
        clauses.append("r.worker_id = %s")
        params.append(worker_id)
    if kind:
        clauses.append("r.kind = %s")
        params.append(kind)
    where = " and ".join(clauses)
    with _connect(settings) as conn:
        rows = conn.execute(
            f"""
            select r.id, r.kind, r.status, r.created_at, r.started_at, r.finished_at,
                   r.cost_usd, r.error,
                   coalesce(p.display_name, w.name, 'an agent') as worker_name,
                   i.title as issue_title
            from public.runs r
            left join public.workers w on w.id = r.worker_id
            left join public.principals p on p.id = w.principal_id
            left join public.issues i on i.id = r.issue_id
            where {where}
            order by r.created_at desc
            limit 50
            """,
            tuple(params),
        ).fetchall()
    out = []
    for r in rows:
        seconds = None
        if r["started_at"] and r["finished_at"]:
            seconds = round((r["finished_at"] - r["started_at"]).total_seconds())
        out.append(
            {
                "id": str(r["id"]),
                "kind": r["kind"],
                "status": r["status"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "seconds": seconds,
                "cost_usd": float(r["cost_usd"]) if r["cost_usd"] is not None else None,
                "error": r["error"],
                "worker_name": r["worker_name"],
                "issue_title": r["issue_title"],
            }
        )
    return out


# ---------------------------------------------------------------------------
# US-62.4 / US-62.5: a human's work, in one report — three already-attributed
# sources (approvals, human test execution, review comments), and how long
# each gate waited for its decision. No new schema: every source table
# already carries a real user id and a real timestamp.
# ---------------------------------------------------------------------------


def _label_filters(org_id: str | None, project_id: str | None, alias: str) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if org_id and _valid_uuid(org_id):
        clauses.append(f"{alias}.org_id = %s")
        params.append(org_id)
    if project_id and _valid_uuid(project_id):
        clauses.append(f"{alias}.project_id = %s")
        params.append(project_id)
    return clauses, params


def user_activity(
    settings: Settings,
    org_id: str | None,
    *,
    days: int = 30,
    project_id: str | None = None,
) -> dict[str, Any]:
    """US-62.4: what each human did — approved/merged code reviews, test
    cases they executed (with pass/fail), and review comments left — by
    org/project. Every count comes from a table that already attributes it
    to a real user id; nothing here is a time estimate (see `gate_latency`
    and, eventually, us-62.6, for those).

    `org_id=None` means every org at once — platform-admin route only,
    mirroring `spend_breakdown`'s us-60.2 rule.
    """
    try:
        window = max(1, min(int(days), 366))
    except (TypeError, ValueError):
        window = 30
    merged: dict[tuple[str, str], dict[str, Any]] = {}

    def bucket(user_id: str, o_id: str, p_id: str) -> dict[str, Any]:
        key = (user_id, p_id)
        if key not in merged:
            merged[key] = {
                "user_id": user_id,
                "org_id": o_id,
                "project_id": p_id,
                "approved": 0,
                "test_pass": 0,
                "test_fail": 0,
                "comments": 0,
                "reviewing_ms": 0,
            }
        return merged[key]

    with _connect(settings) as conn:
        clauses, params = _label_filters(org_id, project_id, "i")
        where = " and ".join(
            [
                "a.gate = 'code-review'",
                "a.decision = 'approved'",
                "not a.auto_approved",
                "a.actor is not null",
                f"a.created_at > now() - interval '{window} days'",
                *clauses,
            ]
        )
        for row in conn.execute(
            f"""
            select a.actor::text as user_id, i.org_id::text as org_id,
                   i.project_id::text as project_id, count(*) as approved
            from public.approvals a
            join public.issues i on i.id = a.issue_id
            where {where}
            group by 1, 2, 3
            """,
            tuple(params),
        ).fetchall():
            bucket(row["user_id"], row["org_id"], row["project_id"])["approved"] = int(
                row["approved"]
            )

        clauses, params = _label_filters(org_id, project_id, "tr")
        where = " and ".join(
            [
                "tr.source = 'human'",
                "tr.started_by is not null",
                f"res.recorded_at > now() - interval '{window} days'",
                *clauses,
            ]
        )
        for row in conn.execute(
            f"""
            select tr.started_by::text as user_id, tr.org_id::text as org_id,
                   tr.project_id::text as project_id,
                   count(*) filter (where res.result = 'pass') as test_pass,
                   count(*) filter (where res.result = 'fail') as test_fail
            from public.test_run_results res
            join public.test_runs tr on tr.id = res.test_run_id
            where {where}
            group by 1, 2, 3
            """,
            tuple(params),
        ).fetchall():
            b = bucket(row["user_id"], row["org_id"], row["project_id"])
            b["test_pass"] = int(row["test_pass"])
            b["test_fail"] = int(row["test_fail"])

        clauses, params = _label_filters(org_id, project_id, "i")
        where = " and ".join(
            [
                "c.author_kind = 'user'",
                f"c.created_at > now() - interval '{window} days'",
                *clauses,
            ]
        )
        for row in conn.execute(
            f"""
            select c.author_user::text as user_id, i.org_id::text as org_id,
                   i.project_id::text as project_id, count(*) as comments
            from public.issue_comments c
            join public.issues i on i.id = c.issue_id
            where {where}
            group by 1, 2, 3
            """,
            tuple(params),
        ).fetchall():
            bucket(row["user_id"], row["org_id"], row["project_id"])[
                "comments"
            ] = int(row["comments"])

        # US-62.6: real, instrumented active time — distinct from us-62.5's
        # queue-inclusive latency. `active_ms` already excludes idle/hidden
        # time on the client side; this only sums it.
        clauses, params = _label_filters(org_id, project_id, "i")
        where = " and ".join(
            [
                f"s.created_at > now() - interval '{window} days'",
                *clauses,
            ]
        )
        for row in conn.execute(
            f"""
            select s.user_id::text as user_id, i.org_id::text as org_id,
                   i.project_id::text as project_id,
                   sum(s.active_ms) as reviewing_ms
            from public.user_activity_sessions s
            join public.issues i on i.id = s.issue_id
            where {where}
            group by 1, 2, 3
            """,
            tuple(params),
        ).fetchall():
            bucket(row["user_id"], row["org_id"], row["project_id"])[
                "reviewing_ms"
            ] = int(row["reviewing_ms"])

        rows = list(merged.values())
        user_ids = [r["user_id"] for r in rows]
        project_ids = [r["project_id"] for r in rows]
        user_labels: dict[str, str] = {}
        project_labels: dict[str, str] = {}
        if user_ids:
            for row in conn.execute(
                "select id::text as id, coalesce(display_name, email) as name "
                "from public.profiles where id = any(%s::uuid[])",
                (user_ids,),
            ).fetchall():
                user_labels[row["id"]] = row["name"]
        if project_ids:
            for row in conn.execute(
                "select id::text as id, name from public.projects "
                "where id = any(%s::uuid[])",
                (project_ids,),
            ).fetchall():
                project_labels[row["id"]] = row["name"]

    for r in rows:
        r["user_label"] = user_labels.get(r["user_id"], "a user")
        r["project_label"] = project_labels.get(r["project_id"], "a project")
    rows.sort(
        key=lambda r: (r["approved"] + r["test_pass"] + r["test_fail"] + r["comments"]),
        reverse=True,
    )
    return {"days": window, "rows": rows[:200]}


# US-62.5: the ready→decided pair each gate is measured over, keyed by the
# `approvals.gate` value. `run-succeeded` is code-review's readiness signal —
# the issue moved to in-review because the run finished, not because a
# separate "ready" event exists for review specifically.
GATE_READY_EVENTS = {
    "prd": "prd-drafted",
    "plan": "plan-ready",
    "code-review": "run-succeeded",
    "elaboration": "elaboration-ready",
}


def gate_latency(
    settings: Settings,
    org_id: str | None,
    *,
    days: int = 30,
    project_id: str | None = None,
) -> dict[str, Any]:
    """US-62.5: how long each gate waited for a human decision — latency to
    decision, NOT active effort (it includes however long the item simply
    sat in a queue). Auto-approved decisions are excluded from the latency
    numbers and counted separately, so they can never make a gate look
    faster than a human actually made it.
    """
    try:
        window = max(1, min(int(days), 366))
    except (TypeError, ValueError):
        window = 30
    rows: list[dict[str, Any]] = []
    auto: list[dict[str, Any]] = []
    with _connect(settings) as conn:
        for gate, ready_type in GATE_READY_EVENTS.items():
            clauses, params = _label_filters(org_id, project_id, "i")
            where = " and ".join(
                [
                    "a.gate = %s",
                    "a.decision = 'approved'",
                    "not a.auto_approved",
                    "a.actor is not null",
                    f"a.created_at > now() - interval '{window} days'",
                    *clauses,
                ]
            )
            duration = "extract(epoch from (a.created_at - ready.at))"
            for row in conn.execute(
                f"""
                select a.actor::text as user_id, i.org_id::text as org_id,
                       i.project_id::text as project_id,
                       count(*) as decisions,
                       avg({duration}) as avg_seconds,
                       min({duration}) as min_seconds,
                       max({duration}) as max_seconds,
                       percentile_cont(0.95) within group (order by {duration})
                         as p95_seconds
                from public.approvals a
                join public.issues i on i.id = a.issue_id
                join lateral (
                    select max(e.created_at) as at
                    from public.issue_events e
                    where e.issue_id = a.issue_id and e.type = %s
                      and e.created_at <= a.created_at
                ) ready on ready.at is not null
                where {where}
                group by 1, 2, 3
                """,
                (ready_type, *params),
            ).fetchall():
                rows.append(
                    {
                        "gate": gate,
                        "user_id": row["user_id"],
                        "org_id": row["org_id"],
                        "project_id": row["project_id"],
                        "decisions": int(row["decisions"]),
                        "avg_seconds": (
                            round(float(row["avg_seconds"]))
                            if row["avg_seconds"] is not None
                            else None
                        ),
                        "min_seconds": (
                            round(float(row["min_seconds"]))
                            if row["min_seconds"] is not None
                            else None
                        ),
                        "max_seconds": (
                            round(float(row["max_seconds"]))
                            if row["max_seconds"] is not None
                            else None
                        ),
                        "p95_seconds": (
                            round(float(row["p95_seconds"]))
                            if row["p95_seconds"] is not None
                            else None
                        ),
                    }
                )
            auto_clauses, auto_params = _label_filters(org_id, project_id, "i")
            auto_where = " and ".join(
                [
                    "a.gate = %s",
                    "a.auto_approved",
                    f"a.created_at > now() - interval '{window} days'",
                    *auto_clauses,
                ]
            )
            for row in conn.execute(
                f"""
                select i.org_id::text as org_id, i.project_id::text as project_id,
                       count(*) as auto_approved
                from public.approvals a
                join public.issues i on i.id = a.issue_id
                where {auto_where}
                group by 1, 2
                """,
                (gate, *auto_params),
            ).fetchall():
                auto.append(
                    {
                        "gate": gate,
                        "org_id": row["org_id"],
                        "project_id": row["project_id"],
                        "auto_approved": int(row["auto_approved"]),
                    }
                )

        # US-62.5: the release milestones (QA sign-off, promotion) — the
        # modern equivalent of the `qa-signoff`/`promotion` approvals.gate
        # values, which no code path writes any more.
        release_clauses, release_params = _label_filters(org_id, project_id, "r")
        base_where = " and ".join(
            [f"r.created_at > now() - interval '{window} days'", *release_clauses]
        )
        for label, actor_col, from_col, to_col in (
            ("qa-signoff", "signed_off_by", "uat_deployed_at", "signed_off_at"),
            ("promotion", "promoted_by", "signed_off_at", "promoted_at"),
        ):
            duration = f"extract(epoch from (r.{to_col} - r.{from_col}))"
            for row in conn.execute(
                f"""
                select r.{actor_col}::text as user_id, r.org_id::text as org_id,
                       r.project_id::text as project_id,
                       count(*) as decisions,
                       avg({duration}) as avg_seconds,
                       min({duration}) as min_seconds,
                       max({duration}) as max_seconds,
                       percentile_cont(0.95) within group (order by {duration})
                         as p95_seconds
                from public.releases r
                where r.{actor_col} is not null and r.{from_col} is not null
                  and r.{to_col} is not null and {base_where}
                group by 1, 2, 3
                """,
                tuple(release_params),
            ).fetchall():
                rows.append(
                    {
                        "gate": label,
                        "user_id": row["user_id"],
                        "org_id": row["org_id"],
                        "project_id": row["project_id"],
                        "decisions": int(row["decisions"]),
                        "avg_seconds": (
                            round(float(row["avg_seconds"]))
                            if row["avg_seconds"] is not None
                            else None
                        ),
                        "min_seconds": (
                            round(float(row["min_seconds"]))
                            if row["min_seconds"] is not None
                            else None
                        ),
                        "max_seconds": (
                            round(float(row["max_seconds"]))
                            if row["max_seconds"] is not None
                            else None
                        ),
                        "p95_seconds": (
                            round(float(row["p95_seconds"]))
                            if row["p95_seconds"] is not None
                            else None
                        ),
                    }
                )

        user_ids = list({r["user_id"] for r in rows if r["user_id"]})
        labels: dict[str, str] = {}
        if user_ids:
            for row in conn.execute(
                "select id::text as id, coalesce(display_name, email) as name "
                "from public.profiles where id = any(%s::uuid[])",
                (user_ids,),
            ).fetchall():
                labels[row["id"]] = row["name"]
    for r in rows:
        r["user_label"] = labels.get(r["user_id"], "a user")
    return {"days": window, "rows": rows, "auto_approved": auto}


# ---------------------------------------------------------------------------
# US-62.9: one page for "is the app fast" — frontend (us-62.7), API/DB
# (us-62.8) and LLM (us-62.3) read together, each already captured by an
# earlier Phase 62 story. This adds no new capture, only the summary and
# per-route/per-model detail reads over what those three already write.
# ---------------------------------------------------------------------------


def performance_summary(settings: Settings, days: int = 7) -> dict[str, Any]:
    """US-62.9: one row per layer — median and p95, over the same window,
    so "is the app slow, and where" is answered by one glance instead of
    three unrelated tables."""
    try:
        window = max(1, min(int(days), 90))
    except (TypeError, ValueError):
        window = 7
    with _connect(settings) as conn:
        frontend = conn.execute(
            f"""
            select percentile_cont(0.5) within group (order by value) as median,
                   percentile_cont(0.95) within group (order by value) as p95,
                   count(*) as samples
            from public.client_perf_events
            where metric = 'LCP' and created_at > now() - interval '{window} days'
            """
        ).fetchone()
        api = conn.execute(
            f"""
            select percentile_cont(0.5) within group (order by duration_ms) as median,
                   percentile_cont(0.95) within group (order by duration_ms) as p95,
                   -- US-62.9: the DB-time share is the number worth a glance —
                   -- the reader should not have to divide two raw columns
                   -- themselves to learn a route spends most of its time in
                   -- the database versus everything else.
                   avg(db_ms::numeric / nullif(duration_ms, 0)) as db_share,
                   count(*) as samples
            from public.api_request_log
            where created_at > now() - interval '{window} days'
            """
        ).fetchone()
        database = conn.execute(
            f"""
            select percentile_cont(0.5) within group (order by db_ms) as median,
                   percentile_cont(0.95) within group (order by db_ms) as p95,
                   count(*) as samples
            from public.api_request_log
            where created_at > now() - interval '{window} days'
            """
        ).fetchone()
        llm = conn.execute(
            f"""
            select percentile_cont(0.5) within group (order by latency_ms) as median,
                   percentile_cont(0.95) within group (order by latency_ms) as p95,
                   count(*) as samples
            from public.llm_usage
            where latency_ms is not null
              and created_at > now() - interval '{window} days'
            """
        ).fetchone()

    def layer(row, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        samples = int(row["samples"]) if row and row["samples"] is not None else 0
        out = {
            "median": (
                round(float(row["median"])) if row and row["median"] is not None else None
            ),
            "p95": round(float(row["p95"])) if row and row["p95"] is not None else None,
            "samples": samples,
        }
        if extra:
            out.update(extra)
        return out

    api_db_share = api.get("db_share") if api else None
    db_share = round(float(api_db_share) * 100) if api_db_share is not None else None
    return {
        "days": window,
        "frontend": layer(frontend),
        "api": layer(api, extra={"db_time_share_pct": db_share}),
        "database": layer(database),
        "llm": layer(llm),
    }


def performance_detail(
    settings: Settings, layer: str, days: int = 7
) -> list[dict[str, Any]]:
    """US-62.9: the per-route (frontend, API, database) or per-model (LLM)
    breakdown behind one of `performance_summary`'s rows."""
    try:
        window = max(1, min(int(days), 90))
    except (TypeError, ValueError):
        window = 7
    with _connect(settings) as conn:
        if layer == "frontend":
            rows = conn.execute(
                f"""
                select route as key,
                       count(*) as samples,
                       percentile_cont(0.5) within group (order by value) as median,
                       percentile_cont(0.95) within group (order by value) as p95
                from public.client_perf_events
                where metric = 'LCP' and created_at > now() - interval '{window} days'
                group by 1
                order by 4 desc nulls last
                limit 100
                """
            ).fetchall()
        elif layer == "api":
            rows = conn.execute(
                f"""
                select route as key,
                       count(*) as samples,
                       percentile_cont(0.5) within group (order by duration_ms) as median,
                       percentile_cont(0.95) within group (order by duration_ms) as p95,
                       avg(db_ms::numeric / nullif(duration_ms, 0)) as db_share
                from public.api_request_log
                where created_at > now() - interval '{window} days'
                group by 1
                order by 4 desc nulls last
                limit 100
                """
            ).fetchall()
        elif layer == "llm":
            rows = conn.execute(
                f"""
                select coalesce(nullif(model, ''), 'unknown') as key,
                       count(*) as samples,
                       percentile_cont(0.5) within group (order by latency_ms) as median,
                       percentile_cont(0.95) within group (order by latency_ms) as p95
                from public.llm_usage
                where latency_ms is not null
                  and created_at > now() - interval '{window} days'
                group by 1
                order by 4 desc nulls last
                limit 100
                """
            ).fetchall()
        else:
            return []
    out = []
    for r in rows:
        out.append(
            {
                "key": r["key"],
                "samples": int(r["samples"]),
                "median": round(float(r["median"])) if r["median"] is not None else None,
                "p95": round(float(r["p95"])) if r["p95"] is not None else None,
                "db_time_share_pct": (
                    round(float(r["db_share"]) * 100)
                    if r.get("db_share") is not None
                    else None
                ),
            }
        )
    return out


def run_spend(settings: Settings, run_id: str) -> dict[str, Any]:
    """What this run has spent so far — read live, while it is still running."""
    if not _valid_uuid(run_id):
        return {
            "tokens_in": 0,
            "tokens_out": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "cost_usd": None,
            "calls": 0,
        }
    with _connect(settings) as conn:
        row = conn.execute(
            """
            select coalesce(sum(tokens_in), 0) as tokens_in,
                   coalesce(sum(tokens_out), 0) as tokens_out,
                   -- US-38.1: how much of this run's input came back out of
                   -- the provider's cache. On a loop re-sending its whole
                   -- conversation every turn, this is most of the input.
                   coalesce(sum(cache_read_tokens), 0) as cache_read_tokens,
                   coalesce(sum(cache_write_tokens), 0) as cache_write_tokens,
                   nullif(coalesce(sum(cost_usd), 0), 0) as cost_usd,
                   count(*) as calls,
                   count(*) filter (where not parsed) as unparsed
            from public.llm_usage where run_id = %s
            """,
            (run_id,),
        ).fetchone()
    return {
        "tokens_in": int(row["tokens_in"]),
        "tokens_out": int(row["tokens_out"]),
        "cache_read_tokens": int(row["cache_read_tokens"]),
        "cache_write_tokens": int(row["cache_write_tokens"]),
        "cost_usd": float(row["cost_usd"]) if row["cost_usd"] is not None else None,
        "calls": int(row["calls"]),
        "unparsed": int(row["unparsed"]),
    }


# --------------------------------------------------------------------------
# Runner command audit (US-10.7)
# --------------------------------------------------------------------------


def record_command_audit(
    settings: Settings,
    org_id: str,
    worker_id: str,
    session_id: str | None,
    run_id: str | None,
    argv: list[str],
    cwd: str | None,
    decision: str,
) -> str:
    """Record a command about to run (or denied); returns the audit id so the
    runner can report its result afterward."""
    with _connect(settings) as conn:
        row = conn.execute(
            """
            insert into public.runner_command_audit
              (org_id, worker_id, session_id, run_id, argv, cwd, policy_decision)
            values (%s, %s, %s, %s, %s, %s, %s)
            returning id
            """,
            (org_id, worker_id, session_id, run_id, list(argv or []), cwd, decision),
        ).fetchone()
        conn.commit()
    return str(row["id"])


def finish_command_audit(
    settings: Settings,
    audit_id: str,
    exit_code: int | None,
    output: str | None,
) -> None:
    """Fill in a command's exit + (truncated) output once it has run."""
    with _connect(settings) as conn:
        conn.execute(
            """
            update public.runner_command_audit
            set finished_at = now(), exit_code = %s, output = %s
            where id = %s
            """,
            (exit_code, (output or "")[:20000], audit_id),
        )
        conn.commit()


# --------------------------------------------------------------------------
# Runner health & fault incidents (US-10.11)
# --------------------------------------------------------------------------


def record_runner_incident(
    settings: Settings,
    org_id: str,
    worker_id: str,
    run_id: str | None,
    kind: str,
    message: str | None,
) -> str:
    with _connect(settings) as conn:
        row = conn.execute(
            """
            insert into public.runner_incidents (org_id, worker_id, run_id, kind, message)
            values (%s, %s, %s, %s, %s)
            returning id
            """,
            (org_id, worker_id, run_id, kind, (message or "")[:2000]),
        ).fetchone()
        conn.commit()
    return str(row["id"])


def notify_org_managers(
    settings: Settings, org_id: str, notif_type: str, payload: dict[str, Any]
) -> int:
    """Best-effort in-app notification to the org's human managers (US-9.12
    channel). Returns how many were notified.

    US-91.15: a new `notif_type` needs a renderer in
    `apps/web/src/lib/notification-copy.ts` (and a case in its test), or the
    bell shows its raw name. The payload keys that surface are `worker`,
    `deployment`, `message`, `run_id`, `issue_id` and `principal_id` — a
    notification carrying none of them cannot say what happened or go
    anywhere.
    """
    with _connect(settings) as conn:
        cur = conn.execute(
            """
            insert into public.notifications (org_id, recipient_id, type, payload)
            select %s, om.principal_id, %s, %s::jsonb
            from public.organization_members om
            join public.principals p on p.id = om.principal_id
            where om.org_id = %s
              and om.role in ('owner', 'admin', 'lead')
              and coalesce(om.status, 'active') = 'active'
              and p.kind = 'human'
              and om.principal_id is not null
            """,
            (org_id, notif_type, json.dumps(payload), org_id),
        )
        n = cur.rowcount
        conn.commit()
    return n


def runner_health(settings: Settings, worker_id: str) -> str:
    """Derive a runner's health from recent runner-fault incidents."""
    with _connect(settings) as conn:
        row = conn.execute(
            """
            select count(*) as n from public.runner_incidents
            where worker_id = %s and created_at > now() - interval '1 hour'
            """,
            (worker_id,),
        ).fetchone()
    n = int(row["n"])
    return "unhealthy" if n >= 3 else "degraded" if n >= 1 else "healthy"


def list_worker_pool(
    settings: Settings,
    worker: dict[str, Any],
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    """The claimable runs this worker may take. Sweeps expired claims back
    into the pool first, so the listing is self-healing without a background
    scheduler. Capability filter (US-31.3, fail-closed): only allow-listed
    project+kind runs are offered — a worker with zero capability rows is
    offered nothing. URL scope (US-3.14): when project_id is given (a
    project-scoped MCP url), only that project's runs are listed.
    US-26.5: a paused runner is offered nothing at all — an agent that has
    just been installed, or one drained for an update, stays connected and
    configured but takes no work."""
    requeue_expired_claims(settings)
    if worker_is_paused(settings, str(worker["id"])):
        return []
    with _connect(settings) as conn:
        return conn.execute(
            """
            select r.id, r.kind, r.issue_id, r.created_at,
                   i.title as issue_title, i.type as issue_type,
                   p.name as project_name, p.repo_full_name,
                   prev.id as retry_of_run_id
            from public.runs r
            -- US-13.12: release (and later deploy) runs are project-scoped
            -- with no issue — the project join rides r.project_id.
            left join public.issues i on i.id = r.issue_id
            join public.projects p on p.id = r.project_id
            -- US-5.5: a queued run with an earlier same-kind run on the
            -- issue is a retry — name the run it retries so an agent can
            -- recognize its own follow-up in the pool.
            left join lateral (
              select r2.id from public.runs r2
              where r.issue_id is not null
                and r2.issue_id = r.issue_id and r2.kind = r.kind
                and r2.created_at < r.created_at
              order by r2.created_at desc
              limit 1
            ) prev on true
            where r.org_id = %(org)s and r.status = 'queued'
              and (%(project)s::uuid is null or r.project_id = %(project)s::uuid)
              -- US-31.3: fail-closed, through the ONE shared predicate.
              -- Zero access rows means this offers nothing — not everything.
              -- US-55.1: the predicate is project ACCESS plus the agent's own
              -- kind checkboxes (enabled_kinds; null = every kind, [] =
              -- benched) — us-53.4's offer gate now lives inside it.
              and public.worker_has_grant(%(worker)s, r.project_id, r.kind)
              -- US-31.5: never offer an item this agent has already burned
              -- its attempt cap on — it would only be refused at claim, and
              -- the agent would spin on it. Another agent may still take it.
              and (
                r.issue_id is null
                or not public.worker_exhausted_on_issue(%(worker)s, r.issue_id)
              )
              -- US-31.5: a blocked item is nobody's work until released.
              and (
                r.issue_id is null
                or not exists (
                  select 1 from public.issues i2
                  where i2.id = r.issue_id
                    and i2.attempts_blocked_at is not null
                )
              )
              -- US-15.3 + US-17.2/17.3: eligibility (sibling-curation hold,
              -- and the feature/epic build-mode phase batching) is centralized
              -- in run_hold_reason — null means claimable.
              and public.run_hold_reason(r.id) is null
              -- US-15.2: a paused run stays queued but is never offered.
              and r.paused_at is null
            -- US-15.2: the manager's order first (unranked runs fall to the
            -- back by age), so a worker pulls in the sequence the manager set.
            order by r.queue_rank asc nulls last, r.created_at asc
            """,
            {
                "org": str(worker["org_id"]),
                "worker": str(worker["id"]),
                "project": project_id,
            },
        ).fetchall()


def worker_idle_reason(settings: Settings, worker_id: str) -> dict[str, Any]:
    """US-27.9: why this agent is not working, in the manager's language.

    On 2026-07-26 both agents on pod-001 showed connected, their services
    showed active and the host card was green — while neither had been able to
    claim anything for fourteen minutes, because their worker tokens had been
    revoked. The socket handshake had already succeeded so the sockets stayed
    up and kept heartbeating; only the HTTP pool poll was rejected, silently,
    every three seconds. Every surface agreed with "waiting for work" and every
    surface was wrong.

    Presence is not permission. Returns one of:

      working      — it holds a run right now
      revoked      — its token is dead; the machine is running and useless
      paused       — deliberately, by an admin (US-26.5)
      no-grants    — its capability grants match nothing that is queued
      queue-held   — there is work, but every item of it is held or paused
      idle         — the healthy one: there is genuinely nothing to do
    """
    if not _valid_uuid(worker_id):
        return {"reason": "unknown", "detail": "no such agent"}
    with _connect(settings) as conn:
        worker = conn.execute(
            "select id, org_id, status from public.workers where id = %s",
            (worker_id,),
        ).fetchone()
        if not worker:
            return {"reason": "unknown", "detail": "no such agent"}
        if worker["status"] != "active":
            return {
                "reason": "revoked",
                "detail": (
                    "this agent's token has been revoked — it can hold its "
                    "socket open but every attempt to take work is rejected"
                ),
            }

        held = conn.execute(
            "select id from public.runs "
            "where worker_id = %s and status = 'running' limit 1",
            (worker_id,),
        ).fetchone()
        if held:
            return {"reason": "working", "detail": "holding a run"}

        paused = conn.execute(
            "select paused from public.runner_config where worker_id = %s",
            (worker_id,),
        ).fetchone()
        if paused and paused["paused"]:
            return {
                "reason": "paused",
                "detail": "paused by an admin — connected, but claiming nothing",
            }

        # Everything queued in the org, before this worker's own filters.
        queued = conn.execute(
            """
            select r.id, r.kind, r.project_id, r.paused_at,
                   public.run_hold_reason(r.id) as hold_reason,
                   coalesce(i.title, p.name) as label
            from public.runs r
            join public.projects p on p.id = r.project_id
            left join public.issues i on i.id = r.issue_id
            where r.org_id = %s and r.status = 'queued'
            order by r.queue_rank asc nulls last, r.created_at asc
            """,
            (str(worker["org_id"]),),
        ).fetchall()
        if not queued:
            return {"reason": "idle", "detail": "nothing is queued"}

        offerable = [
            r for r in queued if not r["hold_reason"] and r["paused_at"] is None
        ]
        if not offerable:
            # There IS work. Naming the nearest item's reason is what turns
            # "waiting for work" from a lie into an explanation.
            first = queued[0]
            why = first["hold_reason"] or "paused by the manager"
            return {
                "reason": "queue-held",
                "detail": (
                    f"{len(queued)} item(s) are queued but none is claimable "
                    f"by anyone — the next one ({first['label']}) is {why}"
                ),
            }

        # US-55.1: a project row means ACCESS; what the agent does is its own
        # kind checkboxes. Mirror worker_has_grant's two halves so the reason
        # names the half that is actually missing.
        access = conn.execute(
            "select distinct project_id from public.worker_capabilities "
            "where worker_id = %s",
            (worker_id,),
        ).fetchall()
        if not access:
            # US-31.3: the gate is fail-closed, so zero access rows literally
            # means it can claim nothing — this readout finally tells the
            # truth it was written to tell.
            return {
                "reason": "no-grants",
                "detail": (
                    f"{len(offerable)} item(s) are claimable, but this agent "
                    "has no project access at all — give it access on its "
                    "Team page"
                ),
            }
        kinds_row = conn.execute(
            "select enabled_kinds from public.runner_config where worker_id = %s",
            (worker_id,),
        ).fetchone()
        enabled = kinds_row["enabled_kinds"] if kinds_row else None
        projects = {str(a["project_id"]) for a in access}
        matching = [
            r
            for r in offerable
            if str(r["project_id"]) in projects
            and (enabled is None or r["kind"] in enabled)
        ]
        if not matching:
            kinds = sorted({r["kind"] for r in offerable})
            return {
                "reason": "no-grants",
                "detail": (
                    f"{len(offerable)} item(s) are claimable, but none is on "
                    "a project this agent can access with a kind it does "
                    f"({', '.join(kinds)} queued) — adjust its projects or "
                    "its checkboxes on its Team page"
                ),
            }
        return {
            "reason": "idle",
            "detail": f"{len(offerable)} item(s) claimable — it should pick one up",
        }


def list_factory_queue(
    settings: Settings,
    org_id: str,
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    """US-15.2: the whole factory queue — every queued and running run for the
    org, in the manager's execution order — so an agent has context of the
    entire pipeline and the intended order, not just the single item it may
    claim next. Unlike list_worker_pool this is NOT capability-filtered and
    includes running / paused / held runs, each carrying its state, so the
    agent's picture matches the manager's factory-queue page.

    Ordering mirrors the pool: within a project, running runs first (they are
    in flight), then the queue by the manager's rank (unranked to the back by
    age). The held predicate is the us-15.3 rule, kept textually parallel with
    list_worker_pool — a story run is held while any non-abandoned draft
    sibling under its parent feature remains."""
    with _connect(settings) as conn:
        return conn.execute(
            """
            select r.id, r.kind, r.status, r.queue_rank, r.paused_at,
                   r.created_at, r.claimed_at,
                   r.issue_id, i.title as issue_title, i.type as issue_type,
                   i.item_no, i.sub_no, i.parent_id,
                   e.number as epic_number, e.title as epic_title,
                   r.project_id, p.name as project_name,
                   w.name as worker_name,
                   -- US-15.3/17.2/17.3: one source of truth for held + why,
                   -- kept in lock-step with list_worker_pool / claim_run.
                   public.run_hold_reason(r.id) as hold_reason,
                   la.tool as last_tool, la.at as last_at
            from public.runs r
            left join public.issues i on i.id = r.issue_id
            join public.projects p on p.id = r.project_id
            left join public.epics e on e.id = i.epic_id
            left join public.workers w on w.id = r.worker_id
            left join lateral (
              select ra.tool, ra.at from public.run_activity ra
              where ra.run_id = r.id
              order by ra.at desc, ra.id desc
              limit 1
            ) la on true
            where r.org_id = %(org)s
              and r.status in ('queued', 'running')
              and (%(project)s::uuid is null or r.project_id = %(project)s::uuid)
            order by p.name asc,
                     case when r.status = 'running' then 0 else 1 end,
                     r.queue_rank asc nulls last,
                     r.created_at asc
            """,
            {"org": str(org_id), "project": project_id},
        ).fetchall()


def list_worker_runs(
    settings: Settings, worker: dict[str, Any], project_id: str | None = None
) -> dict[str, list[dict[str, Any]]]:
    """US-5.1: the worker's own live claims, plus its recent submissions
    whose outcome isn't settled (issue still sitting in a review-ish
    status). Merged and failed runs drop out — it's a recovery aid after
    a session restart, not an archive. project_id scopes both lists when
    the MCP url is project-scoped (US-3.14)."""
    params = {
        "org": str(worker["org_id"]),
        "worker": str(worker["id"]),
        "project": project_id,
    }
    with _connect(settings) as conn:
        claimed = conn.execute(
            """
            select r.id, r.kind, r.claim_expires_at,
                   i.title as issue_title, p.name as project_name
            from public.runs r
            left join public.issues i on i.id = r.issue_id
            join public.projects p on p.id = r.project_id
            where r.org_id = %(org)s and r.worker_id = %(worker)s
              and r.status = 'running'
              and (%(project)s::uuid is null or r.project_id = %(project)s::uuid)
            order by r.claim_expires_at
            """,
            params,
        ).fetchall()
        submitted = conn.execute(
            """
            select r.id, r.kind, r.finished_at,
                   i.title as issue_title, i.status as issue_status,
                   p.name as project_name
            from public.runs r
            join public.issues i on i.id = r.issue_id
            join public.projects p on p.id = i.project_id
            where r.org_id = %(org)s and r.worker_id = %(worker)s
              and r.status = 'succeeded'
              and i.status in
                ('prd-review', 'plan-review', 'in-review', 'needs-fixes')
              and (%(project)s::uuid is null or i.project_id = %(project)s::uuid)
            order by r.finished_at desc
            limit 10
            """,
            params,
        ).fetchall()
    return {"claimed": claimed, "submitted": submitted}


def get_run_status_view(
    settings: Settings, run_id: str, org_id: str
) -> dict[str, Any] | None:
    """US-5.5: one run's post-submit standing, readable org-wide (a retry
    may be worked by a different worker than the submitter). Returns the
    run row, the latest review decision that applies to *this* run, and
    the retry run dispatched after it — or None for unknown/cross-org ids
    (no existence leak)."""
    if not _valid_uuid(run_id):
        return None
    with _connect(settings) as conn:
        run = conn.execute(
            """
            select r.id, r.kind, r.status, r.worker_id, r.pr_url,
                   r.branch_ref, r.error, r.created_at, r.finished_at,
                   r.claim_expires_at,
                   i.id as issue_id, i.title as issue_title,
                   i.status as issue_status,
                   w.name as worker_name
            from public.runs r
            left join public.issues i on i.id = r.issue_id
            left join public.workers w on w.id = r.worker_id
            where r.id = %s and r.org_id = %s
            """,
            (run_id, org_id),
        ).fetchone()
        if not run:
            return None
        review = None
        if run["kind"] == "code":
            review = conn.execute(
                """
                select a.decision, a.comment
                from public.approvals a
                where a.subject_type = 'run' and a.subject_id = %s
                  and a.gate = 'code-review'
                order by a.created_at desc
                limit 1
                """,
                (run_id,),
            ).fetchone()
        elif run["finished_at"] is not None:
            # plan/prd approvals are issue-scoped; only a decision made
            # after this run's submission is this run's outcome.
            review = conn.execute(
                """
                select a.decision, a.comment
                from public.approvals a
                where a.issue_id = %s and a.gate = %s
                  and a.created_at >= %s
                order by a.created_at desc
                limit 1
                """,
                (str(run["issue_id"]), run["kind"], run["finished_at"]),
            ).fetchone()
        retry = conn.execute(
            """
            select r2.id, r2.status
            from public.runs r2
            where r2.issue_id = %s and r2.kind = %s and r2.created_at > %s
            order by r2.created_at
            limit 1
            """,
            (str(run["issue_id"]), run["kind"], run["created_at"]),
        ).fetchone()
    return {"run": run, "review": review, "retry": retry}


def org_shortname_matches(
    settings: Settings, org_id: str, shortname: str
) -> bool:
    """US-3.14: the MCP url's org shortname must name the worker's own org.
    A mismatch (or unknown shortname) answers False → 404 at the wrapper."""
    with _connect(settings) as conn:
        row = conn.execute(
            "select 1 from public.organizations where id = %s and shortname = %s",
            (org_id, shortname),
        ).fetchone()
    return row is not None


def run_in_project(settings: Settings, run_id: str, project_id: str) -> bool:
    """US-3.14: does this run belong to the project the MCP url is scoped
    to? Guards a project-scoped claim from reaching another project's run."""
    if not _valid_uuid(run_id):
        return False
    with _connect(settings) as conn:
        row = conn.execute(
            """
            select 1
            from public.runs r
            join public.issues i on i.id = r.issue_id
            where r.id = %s and i.project_id = %s
            """,
            (run_id, project_id),
        ).fetchone()
    return row is not None


def worker_run_refusal(
    settings: Settings, worker_id: str, run_id: str
) -> str | None:
    """Capability gate on claims — fail-CLOSED (US-31.3). None = allowed;
    otherwise a human-readable reason naming exactly which grant is missing,
    because "not assigned to this project" and "assigned, but not for this
    kind of run" have different fixes. Unknown run ids answer None — the
    claim itself resolves those to 404.

    Both checks go through `public.worker_has_grant`, the ONE predicate all
    three gates share (US-31.3): the pre-inversion design had three copies
    of the rule, and the one nobody was thinking about (the clone gate) is
    how a zero-grant agent could read every repository in the org."""
    if not _valid_uuid(run_id):
        return None
    with _connect(settings) as conn:
        row = conn.execute(
            """
            select public.worker_has_grant(%(w)s, r.project_id, r.kind) as allowed,
                   r.kind, r.issue_id,
                   -- US-31.5
                   coalesce(
                     public.worker_exhausted_on_issue(%(w)s, r.issue_id), false
                   ) as exhausted,
                   coalesce(i.attempts_blocked_at is not null, false) as blocked,
                   -- US-53.4/55.1: named separately so the refusal can tell an
                   -- unchecked kind apart from missing project access — the
                   -- combined predicate answers only yes or no.
                   coalesce(
                     (select rc.enabled_kinds is null
                             or rc.enabled_kinds ? r.kind
                      from public.runner_config rc
                      where rc.worker_id = %(w)s::uuid),
                     true
                   ) as kind_checked
            from public.runs r
            left join public.issues i on i.id = r.issue_id
            where r.id = %(r)s
            """,
            {"w": worker_id, "r": run_id},
        ).fetchone()
    if not row:
        return None
    # US-31.5: attempt limits are checked before the grant reason, because an
    # exhausted agent holding a valid grant is the case the manager actually
    # hit — "you may, but you have tried enough times".
    if row["blocked"]:
        return (
            "this item has exhausted its attempts and is waiting for the "
            "manager to review the failures"
        )
    if row["exhausted"]:
        return (
            "this agent has already spent its attempt limit on this item — "
            "it is left for a different agent"
        )
    # US-53.4: the kind checkboxes refuse at claim too — the same wording the
    # runner's own gate uses, so every surface names the same fix.
    if not row["kind_checked"]:
        return (
            f"this agent does not do '{row['kind']}' work — it is unchecked "
            "in the agent's settings"
        )
    if row["allowed"]:
        return None
    # US-55.1: with the kind checkboxes already cleared above, the only thing
    # the predicate can still be missing is project access.
    return (
        "this agent does not have access to that project — give it access "
        "on its Team page"
    )


def worker_allowed_for_run(
    settings: Settings, worker_id: str, run_id: str
) -> bool:
    """Boolean face of worker_run_refusal, kept for callers that only gate."""
    return worker_run_refusal(settings, worker_id, run_id) is None


def worker_allowed_for_project(
    settings: Settings, worker_id: str, project_id: str
) -> bool:
    """Capability gate on clone/fetch — fail-CLOSED (US-31.3): any grant on
    the project, through the same shared predicate as the claim gate."""
    with _connect(settings) as conn:
        row = conn.execute(
            "select public.worker_has_grant(%(w)s, %(p)s, null) as allowed",
            {"w": worker_id, "p": project_id},
        ).fetchone()
    return bool(row["allowed"])


def claim_run(
    settings: Settings, run_id: str, worker: dict[str, Any]
) -> dict[str, Any] | None:
    """Atomic claim; None when the run isn't queued in the worker's org
    (race lost, unknown, or already claimed) — or when the worker is paused
    (US-26.5), which the pool listing already hides but a worker holding a
    stale run id could otherwise walk straight past."""
    if not _valid_uuid(run_id):
        return None
    if worker_is_paused(settings, str(worker["id"])):
        return None
    lease = _LEASES.get(worker["type"], _LEASES["autonomous"])
    with _connect(settings) as conn:
        run = conn.execute(
            """
            update public.runs
            set worker_id = %s, claimed_at = now(), status = 'running',
                started_at = coalesce(started_at, now()),
                -- US-31.2: the lease is the manager's number when set —
                -- runner_config.max_run_minutes — else the type default.
                --
                -- US-39.2: that number is now PER STORY, and a batch code run
                -- carries several. Multiplied by run_work_units() and bounded
                -- by max_total_run_minutes, so eight stories are not given the
                -- allowance of one. A one-unit run multiplies by 1 and is
                -- therefore bit-for-bit unchanged.
                claim_expires_at = now() + least(
                  coalesce(
                    (select (rc.max_run_minutes || ' minutes')::interval
                       from public.runner_config rc
                      where rc.worker_id = %s),
                    %s::interval
                  ) * public.run_work_units(runs.id),
                  coalesce(
                    (select (rc.max_total_run_minutes || ' minutes')::interval
                       from public.runner_config rc
                      where rc.worker_id = %s),
                    %s::interval
                  )
                ),
                last_heartbeat_at = now(),
                provider = case when %s = 'human' then 'human' else provider end
            where id = %s and org_id = %s and status = 'queued'
              -- US-15.3 + US-17.2/17.3: a held run (sibling still being
              -- curated, or the feature/epic build-mode phase batch holds it)
              -- is never claimable, even on a direct race — the pool won't have
              -- offered it, this is the safety net.
              and public.run_hold_reason(runs.id) is null
              -- US-15.2: a paused run is never claimable.
              and paused_at is null
              -- US-15.9: belt and suspenders. A breakdown run must never
              -- execute if a breakdown for the same issue has already
              -- succeeded — a duplicate that slipped past dispatch_breakdown
              -- would otherwise double the feature's child stories.
              and not (
                runs.kind = 'breakdown'
                and exists (
                  select 1 from public.runs prior
                  where prior.issue_id = runs.issue_id
                    and prior.kind = 'breakdown'
                    and prior.status = 'succeeded'
                    and prior.id <> runs.id
                )
              )
            returning id, org_id, issue_id, kind, claim_expires_at, input_context
            """,
            (
                worker["id"],
                worker["id"],
                lease,
                worker["id"],
                f"{DEFAULT_TOTAL_RUN_MINUTES} minutes",
                worker["type"],
                run_id,
                worker["org_id"],
            ),
        ).fetchone()
        if not run:
            return None
        if run["kind"] == "release":
            # The releases page reads releases.status, not runs.status — left
            # unwritten here, it shows "Queued" for the run's entire active
            # duration and only ever jumps straight to "uat-deployed" when
            # submit_release_run lands. status='queued' is the WHERE-clause
            # guard above, so this is always a genuine queued->running move.
            release_id = (run["input_context"] or {}).get("release_id")
            if release_id:
                conn.execute(
                    "update public.releases set status = 'running', "
                    "updated_at = now() where id = %s and status = 'queued'",
                    (release_id,),
                )
        if run["kind"] == "plan":
            issue_status = "planning"
        elif run["kind"] == "code":
            issue_status = "running"
        else:
            issue_status = None  # prd: no issue-status change while claimed
        if issue_status:
            # US-22.9: a feature-level code run moves EVERY story it covers,
            # not just runs.issue_id — otherwise four of five stories would
            # sit at `queued` while an agent was building them, and every
            # surface asking "is this story being built" would say no.
            # run_issue_ids falls back to runs.issue_id for a single-story
            # run, so both shapes take this one path.
            conn.execute(
                "update public.issues set status = %s "
                "where id in (select issue_id from public.run_issue_ids(%s))",
                (issue_status, run["id"]),
            )
        # US-13.12: project-scoped runs have no issue to log against — the
        # activity feed derives their lifecycle from the runs row itself.
        if run["issue_id"]:
            conn.execute(
                """
                insert into public.issue_events (org_id, issue_id, type, payload)
                values (%s, %s, 'run-claimed', %s)
                """,
                (
                    run["org_id"],
                    run["issue_id"],
                    json.dumps(
                        {
                            "run_id": str(run["id"]),
                            "kind": run["kind"],
                            "worker": worker["name"],
                        }
                    ),
                ),
            )
        conn.commit()
        return run


def get_release(settings: Settings, release_id: str) -> dict[str, Any] | None:
    """US-21.2: one release row, service-role.

    Called from the MCP surface, which has already established that the
    calling worker holds a run linked to this release — that claim is the
    authorization, exactly as it is for every other run-scoped tool.
    """
    if not _valid_uuid(release_id):
        return None
    with _connect(settings) as conn:
        return conn.execute(
            """
            select id, org_id, project_id, version, commit_sha, git_tag,
                   previous_release_id, status, included_items,
                   notes_summary, notes_detail, uat_deployment_run_id,
                   prod_deployment_run_id, notes_written_at, uat_deployed_at,
                   cases_attached_at, signed_off_at, promoted_at, released_at,
                   created_at, created_by
            from public.releases where id = %s
            """,
            (release_id,),
        ).fetchone()


def get_release_uat_deployment_id(settings: Settings, project_id: str) -> str | None:
    if not _valid_uuid(project_id):
        return None
    with _connect(settings) as conn:
        row = conn.execute(
            "select release_uat_deployment_id from public.projects where id = %s",
            (project_id,),
        ).fetchone()
    return str(row["release_uat_deployment_id"]) if row and row["release_uat_deployment_id"] else None


def list_release_prep_pool(settings: Settings, org_id: str) -> list[dict[str, Any]]:
    """US-63.3: queued release-prep jobs for this org — deliberately not the
    Work Items pool (list_available_work); release prep has no issue and no
    business showing up next to story work."""
    with _connect(settings) as conn:
        return conn.execute(
            """
            select p.id, p.project_id, p.release_id, p.created_at,
                   r.version, r.commit_sha, pr.name as project_name,
                   pr.repo_full_name
            from public.release_prep_runs p
            join public.releases r on r.id = p.release_id
            join public.projects pr on pr.id = p.project_id
            where p.org_id = %s and p.status = 'queued'
            order by p.created_at
            """,
            (org_id,),
        ).fetchall()


def claim_release_prep(
    settings: Settings, prep_id: str, worker: dict[str, Any]
) -> dict[str, Any] | None:
    """Atomic claim, mirroring claim_run's shape without any of the
    lease/attempt-budget machinery release prep has no use for."""
    if not _valid_uuid(prep_id):
        return None
    with _connect(settings) as conn:
        row = conn.execute(
            """
            update public.release_prep_runs
            set status = 'running', worker_id = %s, claimed_at = now(),
                claim_expires_at = now() + interval '2 hours'
            where id = %s and org_id = %s and status = 'queued'
            returning id, org_id, project_id, release_id
            """,
            (worker["id"], prep_id, worker["org_id"]),
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "update public.releases set status = 'running', updated_at = now() "
            "where id = %s and status = 'queued'",
            (row["release_id"],),
        )
        conn.commit()
        return row


def get_release_prep(
    settings: Settings, prep_id: str, org_id: str
) -> dict[str, Any] | None:
    if not _valid_uuid(prep_id):
        return None
    with _connect(settings) as conn:
        return conn.execute(
            """
            select p.id, p.org_id, p.project_id, p.release_id, p.status,
                   p.worker_id, p.claimed_at, p.claim_expires_at,
                   pr.repo_full_name, pr.default_branch
            from public.release_prep_runs p
            join public.projects pr on pr.id = p.project_id
            where p.id = %s and p.org_id = %s
            """,
            (prep_id, org_id),
        ).fetchone()


def heartbeat_release_prep(settings: Settings, prep_id: str, worker_id: str) -> bool:
    with _connect(settings) as conn:
        row = conn.execute(
            """
            update public.release_prep_runs
            set claim_expires_at = now() + interval '2 hours'
            where id = %s and worker_id = %s and status = 'running'
            returning id
            """,
            (prep_id, worker_id),
        ).fetchone()
        conn.commit()
    return bool(row)


def complete_release_prep(settings: Settings, prep_id: str, outcome: str) -> bool:
    with _connect(settings) as conn:
        row = conn.execute(
            """
            update public.release_prep_runs
            set status = %s, finished_at = now()
            where id = %s and status = 'running'
            returning id
            """,
            (outcome, prep_id),
        ).fetchone()
        conn.commit()
    return bool(row)


def fail_release_prep(settings: Settings, prep_id: str, error: str) -> dict[str, Any] | None:
    """Symmetric to complete_run's failure-side release update (2026-08-01
    fix): a failed prep must not leave the release stuck in-flight forever."""
    with _connect(settings) as conn:
        row = conn.execute(
            """
            update public.release_prep_runs
            set status = 'failed', error = left(%s, 2000), finished_at = now()
            where id = %s and status = 'running'
            returning release_id
            """,
            (error, prep_id),
        ).fetchone()
        if row:
            conn.execute(
                """
                update public.releases
                set status = 'failed',
                    failure_reason = left(%s, 500),
                    updated_at = now()
                where id = %s
                  and status in ('queued', 'running', 'notes-ready', 'deploying',
                                  'uat-deploy-failed', 'uat-signed-off', 'promoting')
                """,
                (error, row["release_id"]),
            )
        conn.commit()
    return row


def get_worker_run(
    settings: Settings, run_id: str, org_id: str
) -> dict[str, Any] | None:
    """Run + issue/project context, org-scoped (unknown org → None, never
    existence leaks across orgs)."""
    if not _valid_uuid(run_id):
        return None
    with _connect(settings) as conn:
        row = conn.execute(
            """
            select r.id, r.org_id, r.issue_id, r.worker_id, r.status, r.kind,
                   r.input_context, r.claim_expires_at, r.pr_url, r.branch_ref,
                   r.stop_requested_at,
                   -- US-59.3: read back so the context bundle can hand it to
                   -- the runner as the id to `--resume`.
                   r.claude_session_id,
                   -- US-32.7: what this run was resolved to run under, so the
                   -- context bundle carries it without a second query.
                   r.resolved_settings,
                   -- US-33.2: set by the gateway at the moment it refused a
                   -- call; turns the hand-back that follows into a stop.
                   r.stopped_reason,
                   -- US-34.3: the tool servers this run was granted, composed
                   -- once at claim.
                   r.tool_surface,
                   -- US-81.5/81.6: the reported spec map and pre-submit test
                   -- evidence, so validation can see what was (not) reported.
                   r.spec_map, r.test_evidence,
                   i.title as issue_title, r.project_id, i.parent_id,
                   i.instruction_set, i.type as issue_type,
                   i.item_no, i.sub_no, e.number as epic_number,
                   p.slug as project_slug, o.shortname as org_shortname,
                   p.dev_branch_strategy, p.default_branch,
                   p.uat_branch, p.production_branch, p.summary as project_summary,
                   p.repo_full_name as project_repo_full_name,
                   p.docs_tree_enabled, p.presubmit_test_command
            from public.runs r
            -- US-13.12: issue-less (project-scoped) runs resolve their
            -- project via r.project_id; issue fields come back null.
            left join public.issues i on i.id = r.issue_id
            join public.projects p on p.id = r.project_id
            join public.organizations o on o.id = r.org_id
            left join public.epics e on e.id = i.epic_id
            where r.id = %s and r.org_id = %s
            """,
            (run_id, org_id),
        ).fetchone()
    return row


def get_project_docs_config(
    settings: Settings, project_id: str
) -> dict[str, Any] | None:
    """US-13.4: what the repo docs-tree writer needs from the project row.

    US-22.6/22.7 add the assembled guidelines (the instruction block's other
    half) and the last successful instruction write, so the writer can skip
    GitHub entirely when nothing has changed."""
    if not _valid_uuid(project_id):
        return None
    with _connect(settings) as conn:
        return conn.execute(
            "select id, org_id, name, repo_full_name, default_branch, "
            "docs_tree_enabled, instructions_synced_hash, "
            "instructions_synced_at, instructions_synced_sha, "
            "public.assemble_project_guidelines(id) as guidelines "
            "from public.projects where id = %s",
            (project_id,),
        ).fetchone()


def get_project_instructions_for_publish(
    settings: Settings, project_id: str
) -> dict[str, str]:
    """US-99.2: every instruction kind's RESOLVED text for this project.

    Resolved, not raw: `worker_instruction_for` runs the same four-level
    chain a worker's own read goes through (project row → project-template
    override → baked default), so what gets published is exactly what an
    agent would have been served. Publishing the raw column instead would
    write empty files for every kind a project has never edited.

    A kind whose whole chain resolves to blank is returned as an empty
    string, which the publisher turns into a DELETE — the repository must
    never carry an instruction the factory no longer believes in.
    """
    if not _valid_uuid(project_id):
        return {}
    from .instruction_files import KIND_FILES

    kinds = sorted(KIND_FILES)
    with _connect(settings) as conn:
        rows = conn.execute(
            "select k.kind, "
            "coalesce(public.worker_instruction_for(%s, k.kind), '') as content "
            "from unnest(%s::text[]) as k(kind)",
            (project_id, kinds),
        ).fetchall()
    return {r["kind"]: (r["content"] or "").strip() for r in rows}


def get_approved_plans(
    settings: Settings, issue_id: str, org_id: str
) -> dict[str, str | None]:
    """US-22.9: one story's approved plan and test plan, for the per-story
    pull a multi-story code run needs. Org-scoped so a worker cannot read
    across orgs even with a valid issue id."""
    if not _valid_uuid(issue_id):
        return {"plan": None, "test_plan": None}
    with _connect(settings) as conn:
        rows = conn.execute(
            """
            select distinct on (a.kind) a.kind, a.content
            from public.artifacts a
            join public.issues i on i.id = a.issue_id
            where a.issue_id = %s and i.org_id = %s
              and a.status = 'approved'
              and a.kind in ('plan', 'test_plan')
            order by a.kind, a.version desc
            """,
            (issue_id, org_id),
        ).fetchall()
    out: dict[str, str | None] = {"plan": None, "test_plan": None}
    for row in rows:
        out[row["kind"]] = row["content"]
    return out


def record_instructions_sync(
    settings: Settings, project_id: str, block_hash: str, commit_sha: str
) -> None:
    """US-22.7: remember what was actually committed. Only ever called after a
    successful write — a failure leaves the hash alone so the next dispatch
    retries."""
    if not _valid_uuid(project_id):
        return
    with _connect(settings) as conn:
        conn.execute(
            "update public.projects set instructions_synced_hash = %s, "
            "instructions_synced_sha = %s, instructions_synced_at = now() "
            "where id = %s",
            (block_hash, commit_sha, project_id),
        )
        conn.commit()


def list_approved_docs(
    settings: Settings, project_id: str, org_id: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """US-13.4: every issue in the project (index order) plus the approved
    prd/plan/test_plan artifacts (ordered so the latest version wins when
    the caller folds them into a dict).

    US-22.4 adds `dispatched` — whether the manager has ever sent this issue
    to the factory. There is no breakdown review gate, so `submit_stories`
    creates children as `draft`; dispatch is the first point where a human
    looked at a story and committed it. Deriving it from "has a run" rather
    than from the mutable status column is what makes it survive a rebuild:
    the tree is regenerated wholesale, and a story that was dispatched and
    has since moved on is still a story the manager published.

    US-22.5 adds the third return value: approved code runs, so a story's
    file can say what actually shipped. `pushed_head_sha` covers the direct
    strategy, where there is a commit but no PR to merge."""
    with _connect(settings) as conn:
        issues = conn.execute(
            """
            select i.id, i.title, i.type, i.parent_id, i.item_no, i.sub_no,
                   i.body, i.acceptance_criteria, e.number as epic_number,
                   exists (
                     select 1 from public.runs r where r.issue_id = i.id
                   ) as dispatched
            from public.issues i
            left join public.epics e on e.id = i.epic_id
            where i.project_id = %s and i.org_id = %s
            order by e.number nulls last, i.item_no nulls last,
                     i.sub_no nulls last, i.created_at
            """,
            (project_id, org_id),
        ).fetchall()
        artifacts = conn.execute(
            """
            select a.issue_id, a.kind, a.content, a.version
            from public.artifacts a
            join public.issues i on i.id = a.issue_id
            where i.project_id = %s and a.org_id = %s
              and a.status = 'approved'
              and a.kind in ('prd', 'plan', 'test_plan')
            order by a.version
            """,
            (project_id, org_id),
        ).fetchall()
        outcomes = conn.execute(
            """
            select r.issue_id,
                   coalesce(r.merge_commit_sha, r.pushed_head_sha) as commit_sha,
                   r.pr_url, r.handback_notes, r.change_breakdown,
                   coalesce(r.finished_at, r.updated_at) as merged_at
            from public.runs r
            join public.issues i on i.id = r.issue_id
            where i.project_id = %s and r.org_id = %s
              and r.kind = 'code'
              and coalesce(r.merge_commit_sha, r.pushed_head_sha) is not null
              and i.status in ('merged', 'done')
            order by coalesce(r.finished_at, r.updated_at)
            """,
            (project_id, org_id),
        ).fetchall()
    return issues, artifacts, outcomes


def get_worker_row(settings: Settings, worker_id: str) -> dict[str, Any] | None:
    """A worker by id (any status) — the reconciler submits on behalf of
    the worker whose pushed claim expired (US-3.4)."""
    if not _valid_uuid(worker_id):
        return None
    with _connect(settings) as conn:
        row = conn.execute(
            "select id, org_id, name, type, status from public.workers "
            "where id = %s",
            (worker_id,),
        ).fetchone()
    return row


def force_requeue_run(
    settings: Settings, run_id: str, note: str | None = None
) -> bool:
    """Re-queue a single running claim: the US-3.4 auto-submit fallback,
    and since US-13.6 the manager's one-click recovery for a run whose
    worker went silent."""
    with _connect(settings) as conn:
        run = conn.execute(
            """
            update public.runs
            set status = 'queued', worker_id = null, claimed_at = null,
                claim_expires_at = null, last_heartbeat_at = null
            where id = %s and status = 'running'
            returning id, org_id, issue_id, kind
            """,
            (run_id,),
        ).fetchone()
        if not run:
            return False
        # US-13.6: same kind guard as release/expiry — only plan/code
        # claims ever moved the issue.
        if run["kind"] in ("plan", "code"):
            conn.execute(
                # US-27.1: run_issue_ids covers both shapes — `where id = %s`
                # alone left a feature run's member stories reading as
                # claimed by a claim that no longer exists.
                "update public.issues set status = 'queued' "
                "where id in (select issue_id from public.run_issue_ids(%s))",
                (run["id"],),
            )
        if run["issue_id"]:
            conn.execute(
                """
                insert into public.issue_events (org_id, issue_id, type, payload)
                values (%s, %s, 'claim-expired', %s)
                """,
                (
                    run["org_id"],
                    run["issue_id"],
                    json.dumps(
                        {
                            "run_id": str(run["id"]),
                            "kind": run["kind"],
                            "note": note
                            or "auto-submit failed; returned to the pool",
                        }
                    ),
                ),
            )
        conn.commit()
        return True


# US-15.14: the resting status a reset returns an issue to when the run
# predates prev_issue_status (dispatched before migration 120), by kind.
_RESET_FALLBACK_STATUS = {
    "plan": "draft",
    "code": "planned",
    "prd": "ready",
    "breakdown": "ready",
}


def run_members(settings: Settings, run_id: str) -> list[dict[str, Any]]:
    """US-27.1: the stories a run covers, in build order, each with its title,
    current status and how many commits have landed against it. Empty for a
    single-story run — absence of run_items means runs.issue_id is the whole
    membership, and there is nothing to attribute."""
    if not _valid_uuid(run_id):
        return []
    with _connect(settings) as conn:
        rows = conn.execute(
            """
            select ri.issue_id, ri.position, ri.prev_issue_status,
                   i.title, i.status,
                   case when e.number is not null and i.item_no is not null
                     then 'US-' || e.number || '.' || i.item_no ||
                          coalesce('.' || i.sub_no, '')
                   end as display_id,
                   (select count(*) from public.run_item_commits c
                     where c.run_id = ri.run_id and c.issue_id = ri.issue_id)
                     as commit_count
            from public.run_items ri
            join public.issues i on i.id = ri.issue_id
            left join public.epics e on e.id = i.epic_id
            where ri.run_id = %s
            order by ri.position
            """,
            (run_id,),
        ).fetchall()
    return [
        {
            "issue_id": str(r["issue_id"]),
            "position": r["position"],
            "title": r["title"],
            "status": r["status"],
            "display_id": r["display_id"] or str(r["issue_id"])[:8],
            "landed": bool(r["commit_count"]),
            "commit_count": int(r["commit_count"]),
        }
        for r in rows
    ]


def resolve_member_ids(
    members: list[dict[str, Any]], given: list[str]
) -> tuple[list[str], list[str]]:
    """US-27.1: (resolved uuids, unknown ids). An agent reads display ids
    (`US-1.1.3`) in its brief and uuids in the structured context, so both are
    accepted — being strict about which one would be a trap, not a check.

    US-40.2: lives here rather than in `factory_mcp` because the branch
    hand-back resolves the same names from commit trailers. Two callers
    resolving membership two ways is how attribution drifts."""
    by_uuid = {m["issue_id"]: m["issue_id"] for m in members}
    by_display = {
        str(m["display_id"]).strip().lower(): m["issue_id"] for m in members
    }
    resolved: list[str] = []
    unknown: list[str] = []
    for raw in given:
        key = str(raw).strip()
        hit = by_uuid.get(key) or by_display.get(key.lower())
        if hit:
            if hit not in resolved:
                resolved.append(hit)
        else:
            unknown.append(key)
    return resolved, unknown


def record_changeset_coverage(
    settings: Settings,
    run_id: str,
    org_id: str,
    issue_ids: list[str],
    commit_sha: str,
    message: str,
    files_changed: int | None = None,
) -> int:
    """US-27.1: record which stories a commit landed the work for. Written by
    `api` on the run's own claim, never by a client — a client that could
    write here could manufacture coverage for a story with no code.

    Idempotent on (run, issue, sha): a resubmitted identical commit is the
    same fact, not a second one."""
    if not (_valid_uuid(run_id) and issue_ids and (commit_sha or "").strip()):
        return 0
    written = 0
    with _connect(settings) as conn:
        for issue_id in issue_ids:
            if not _valid_uuid(issue_id):
                continue
            row = conn.execute(
                """
                insert into public.run_item_commits
                  (org_id, run_id, issue_id, commit_sha, message, files_changed)
                values (%s, %s, %s, %s, %s, %s)
                on conflict (run_id, issue_id, commit_sha) do nothing
                returning id
                """,
                (
                    org_id,
                    run_id,
                    issue_id,
                    commit_sha.strip(),
                    (message or "")[:2000],
                    files_changed,
                ),
            ).fetchone()
            if row:
                written += 1
        conn.commit()
    return written


def _restore_run_issues(conn, run: dict[str, Any]) -> str | None:
    """US-27.10: put every issue a run took back where it was.

    A run carries `prev_issue_status` for exactly this (migration 120), and
    since US-27.1 each member story carries its own. Returns the status the
    run's own issue was restored to, for the caller's summary."""
    target = run.get("prev_issue_status") or _RESET_FALLBACK_STATUS.get(
        run["kind"]
    )
    for m in _run_member_rows(conn, str(run["id"])):
        conn.execute(
            "update public.issues set status = %s where id = %s",
            (m["prev_issue_status"] or _RESET_FALLBACK_STATUS["code"],
             m["issue_id"]),
        )
    if run.get("issue_id") and target:
        conn.execute(
            "update public.issues set status = %s where id = %s",
            (target, run["issue_id"]),
        )
    return target


def _record_cancel(
    conn, run: dict[str, Any], reason: str, actor: str | None
) -> dict[str, Any]:
    """Land a run in `cancelled` and put its work items back."""
    conn.execute(
        """
        update public.runs
        set status = 'cancelled', cancel_reason = %s, cancelled_at = now(),
            worker_id = null, claimed_at = null, claim_expires_at = null,
            last_heartbeat_at = null, paused_at = null,
            stop_requested_at = null
        where id = %s
        """,
        (reason, run["id"]),
    )
    target = _restore_run_issues(conn, run)
    ids = conn.execute(
        "select issue_id from public.run_issue_ids(%s)", (run["id"],)
    ).fetchall()
    for row in ids:
        conn.execute(
            """
            insert into public.issue_events (org_id, issue_id, type, payload)
            values (%s, %s, 'run-cancelled', %s)
            """,
            (
                run["org_id"],
                row["issue_id"],
                json.dumps(
                    {
                        "run_id": str(run["id"]),
                        "kind": run["kind"],
                        "reason": reason,
                        "restored_to_status": target,
                        "by": actor or "manager",
                    }
                ),
            ),
        )
    conn.commit()
    return {
        "status": "cancelled",
        "kind": run["kind"],
        "reason": reason,
        "restored_to_status": target,
        "issues": [str(r["issue_id"]) for r in ids],
    }


def cancel_run(
    settings: Settings, run_id: str, reason: str, actor: str | None = None
) -> dict[str, Any] | None:
    """US-27.10: retire a run that should not have been dispatched.

    A `queued` run is cancelled outright. A `running` one is NOT killed: it
    gets the cooperative stop request Phase 15 already built, carrying the
    cancel reason, and lands `cancelled` when the worker hands the claim back
    (`acknowledge_stop`). The fleet never kills work by surprise.

    Returns a summary, `{"stop_requested": True}` for a running run, or None
    when the run is not active."""
    reason = (reason or "").strip()
    if not (_valid_uuid(run_id) and reason):
        return None
    with _connect(settings) as conn:
        run = conn.execute(
            """
            select id, org_id, issue_id, kind, status, prev_issue_status
            from public.runs
            where id = %s and status in ('queued', 'running')
            for update
            """,
            (run_id,),
        ).fetchone()
        if not run:
            return None
        if run["status"] == "running":
            conn.execute(
                """
                update public.runs
                set cancel_reason = %s,
                    stop_requested_at = coalesce(stop_requested_at, now())
                where id = %s
                """,
                (reason, run_id),
            )
            conn.commit()
            return {
                "status": "running",
                "stop_requested": True,
                "kind": run["kind"],
                "reason": reason,
            }
        return _record_cancel(conn, run, reason, actor)


def _reset_run_split(
    conn,
    run: dict[str, Any],
    members: list[dict[str, Any]],
    actor: str | None,
) -> dict[str, Any]:
    """US-27.1: reset a multi-story run whose work has partly landed.

    Re-queuing it would invite a second agent to rebuild commits that are
    already on the branch, and restoring every story would orphan real code.
    So the run finishes where it stands: the stories it committed for go to
    review, the ones it never reached go back to the pool, and the run row
    keeps its branch and PR so there is something to review."""
    landed = [m for m in members if m["commits"]]
    returned = [m for m in members if not m["commits"]]

    conn.execute(
        """
        update public.runs
        set status = 'succeeded', worker_id = null, claimed_at = null,
            claim_expires_at = null, last_heartbeat_at = null,
            paused_at = null, stop_requested_at = null, finished_at = now()
        where id = %s
        """,
        (run["id"],),
    )
    conn.execute(
        "update public.issues set status = 'in-review' where id = any(%s)",
        ([m["issue_id"] for m in landed],),
    )
    for m in returned:
        conn.execute(
            "update public.issues set status = %s where id = %s",
            (m["prev_issue_status"] or _RESET_FALLBACK_STATUS["code"],
             m["issue_id"]),
        )
    if run.get("issue_id"):
        conn.execute(
            "update public.issues set status = 'in-review' where id = %s",
            (run["issue_id"],),
        )
    summary = {
        "kind": run["kind"],
        "split": True,
        "landed": [str(m["issue_id"]) for m in landed],
        "returned": [str(m["issue_id"]) for m in returned],
        "discarded": {},
        "reset_to_status": None,
    }
    for m in members:
        conn.execute(
            """
            insert into public.issue_events (org_id, issue_id, type, payload)
            values (%s, %s, 'run-reset', %s)
            """,
            (
                run["org_id"],
                m["issue_id"],
                json.dumps(
                    {
                        "run_id": str(run["id"]),
                        "kind": run["kind"],
                        "split": True,
                        "landed": bool(m["commits"]),
                        "by": actor or "manager",
                    }
                ),
            ),
        )
    conn.commit()
    return summary


def reset_run(
    settings: Settings, run_id: str, actor: str | None = None
) -> dict[str, Any] | None:
    """US-15.14: reset a wrongly-started run. Discards this attempt's draft
    output, returns the issue to its pre-dispatch status, and re-queues the
    same run for a fresh claim — a clean restart, not an abandonment. Works on
    a `queued` or `running` run of any kind; clearing the claim means a worker
    still on it is cleanly rejected on its next call (it no longer holds the
    run). Returns a summary of what was discarded, or None if the run isn't
    active (already terminal, unknown, or a lost race).

    US-27.1: a multi-story run that has already landed commits cannot be
    cleanly restarted — the branch carries that work whatever the run row
    says. Such a reset SPLITS instead: the stories with a landed commit go to
    review, the rest return to the pool, and the run finishes rather than
    going back for a second agent to rebuild what is already pushed."""
    with _connect(settings) as conn:
        run = conn.execute(
            """
            select id, org_id, issue_id, kind, prev_issue_status
            from public.runs
            where id = %s and status in ('queued', 'running')
            for update
            """,
            (run_id,),
        ).fetchone()
        if not run:
            return None

        members = _run_member_rows(conn, str(run["id"]))
        if any(m["commits"] for m in members):
            return _reset_run_split(conn, run, members, actor)

        issue_id = run["issue_id"]
        kind = run["kind"]
        discarded: dict[str, int] = {}

        if issue_id:
            # Discard the draft output of this run's kind. For an active run
            # this is the latest unapproved attempt of that kind; approved and
            # superseded artifacts are never touched.
            if kind == "prd":
                n = conn.execute(
                    "delete from public.artifacts where issue_id = %s "
                    "and kind = 'prd' and status = 'draft'",
                    (issue_id,),
                ).rowcount
                if n:
                    discarded["prd_drafts"] = n
            elif kind == "plan":
                n = conn.execute(
                    "delete from public.artifacts where issue_id = %s "
                    "and kind in ('plan', 'test_plan') and status = 'draft'",
                    (issue_id,),
                ).rowcount
                if n:
                    discarded["plan_drafts"] = n
            elif kind == "breakdown":
                # Draft children are soft-deleted (abandoned) — reversible, and
                # it sidesteps hard-delete FK/guard concerns. An active
                # breakdown normally has none (children land at submit), so this
                # is usually a no-op safety net.
                n = conn.execute(
                    "update public.issues set abandoned_at = now() "
                    "where parent_id = %s and status = 'draft' "
                    "and abandoned_at is null",
                    (issue_id,),
                ).rowcount
                if n:
                    discarded["draft_children"] = n
            # A code run's DB footprint is the run row's branch_ref/pr_url,
            # cleared below; the pushed branch itself lives on GitHub and is
            # left in place (a fresh run overwrites it).

        # Re-queue the same run: drop the claim and any code-run pointers so the
        # next worker starts clean. Keeps prev_issue_status for the record.
        conn.execute(
            """
            update public.runs
            set status = 'queued', worker_id = null, claimed_at = null,
                claim_expires_at = null, last_heartbeat_at = null,
                started_at = null, branch_ref = null, pr_url = null,
                paused_at = null
            where id = %s
            """,
            (run_id,),
        )

        target = run["prev_issue_status"] or _RESET_FALLBACK_STATUS.get(kind)
        # US-27.1: the run's member stories are moved to `queued` at dispatch,
        # so a reset that only touched runs.issue_id left every one of them
        # sitting in `queued` against a run nobody holds.
        for m in members:
            conn.execute(
                "update public.issues set status = %s where id = %s",
                (m["prev_issue_status"] or _RESET_FALLBACK_STATUS["code"],
                 m["issue_id"]),
            )
        if issue_id:
            if target:
                conn.execute(
                    "update public.issues set status = %s where id = %s",
                    (target, issue_id),
                )
            conn.execute(
                """
                insert into public.issue_events (org_id, issue_id, type, payload)
                values (%s, %s, 'run-reset', %s)
                """,
                (
                    run["org_id"],
                    issue_id,
                    json.dumps(
                        {
                            "run_id": str(run["id"]),
                            "kind": kind,
                            "reset_to_status": target,
                            "discarded": discarded,
                            "by": actor or "manager",
                        }
                    ),
                ),
            )
        conn.commit()
        return {"kind": kind, "discarded": discarded, "reset_to_status": target}


# US-36.1: the values `run_trace_kind_check` permits. Held here so a caller
# sending anything else is coerced rather than raising — on 2026-07-27 the
# supervisor sent no kind at all, the socket handler defaulted it to the
# non-existent `note`, and EVERY trace insert raised. The exception escaped an
# unguarded handler and killed the control socket, which cost five runs. The
# trace table had never held a single row.
RUN_TRACE_KINDS = (
    "step",
    "tool",
    "decision",
    "output",
    "progress",
    "clarification",
    "submission",
    "error",
)
DEFAULT_RUN_TRACE_KIND = "progress"


def scrub_credential_patterns(text: str | None) -> str | None:
    """us-96.11: credentials the API can recognise WITHOUT knowing their
    values — header shapes — masked before anything is stored. The runner
    scrubs the values it holds; this assumes the runner failed. Known
    shapes only, no entropy guessing: hashes and shas legitimately ride
    traces, and flagging them would bury the signal."""
    if not text:
        return text
    for pattern in _CREDENTIAL_HEADER_PATTERNS:
        text = pattern.sub(r"\1[redacted]", text)
    return text


_CREDENTIAL_HEADER_PATTERNS = (
    re.compile(r"(?i)(x-factory-local-key['\"]?\s*[:=]\s*['\"]?)([^\s'\"]+)"),
    re.compile(r"(?i)(x-worker-token['\"]?\s*[:=]\s*['\"]?)([^\s'\"]+)"),
    re.compile(r"(?i)(authorization['\"]?\s*[:=]\s*['\"]?bearer\s+)([^\s'\"]+)"),
)


def record_run_trace(
    settings: Settings,
    run_id: str,
    worker_id: str,
    kind: str,
    content: str,
) -> int | None:
    """US-15.5: append one trace entry to a run the worker holds. Attribution
    (org, issue, principal) is derived in the SQL function from the claim, which
    also refuses a run this worker doesn't hold — returns the new entry id, or
    None on refusal (unknown run, not held, not running).

    US-36.1: an unrecognised `kind` is coerced to `progress`. A trace is
    diagnostic output; losing the line AND the socket because a caller invented
    a label is the worst of both outcomes.

    us-96.11: stored scrubbed — redaction happens at write time, so reading
    the row with service role already shows the mask and no render-time
    masking exists to forget."""
    if not (_valid_uuid(run_id) and _valid_uuid(worker_id)):
        return None
    if kind not in RUN_TRACE_KINDS:
        kind = DEFAULT_RUN_TRACE_KIND
    content = scrub_credential_patterns(content) or ""
    with _connect(settings) as conn:
        row = conn.execute(
            "select public.record_run_trace(%s, %s, %s, %s) as id",
            (run_id, worker_id, kind, content),
        ).fetchone()
        conn.commit()
        return row["id"] if row and row["id"] is not None else None


def request_run_stop(settings: Settings, run_id: str) -> bool:
    """US-15.15: ask the working agent to stop. Records the request on the run;
    the claim-holder sees it on its next report_progress. Only a running claim
    can be asked to stop (a queued run is just cancelled — that's us-15.14).
    Returns True when the flag was set."""
    if not _valid_uuid(run_id):
        return False
    with _connect(settings) as conn:
        row = conn.execute(
            """
            update public.runs set stop_requested_at = coalesce(stop_requested_at, now())
            where id = %s and status = 'running'
            returning id
            """,
            (run_id,),
        ).fetchone()
        conn.commit()
        return row is not None


def acknowledge_stop(
    settings: Settings, run_id: str, worker_id: str, note: str | None = None
) -> dict[str, Any] | None:
    """US-15.15: the claim-holder confirms it saw the stop request, cleaned up
    its own partial work, and is handing the claim back. Lands the issue in the
    same clean pre-dispatch state us-15.14 produces (re-queue the run, restore
    issues.status to prev_issue_status) but records it as a cooperative
    'run-stopped', distinct from a forced 'run-reset'. `note` is the agent's own
    account of what it undid. Returns a summary, or None if this worker doesn't
    hold a running, stop-requested run (nothing to acknowledge)."""
    with _connect(settings) as conn:
        run = conn.execute(
            """
            select id, org_id, issue_id, kind, prev_issue_status, cancel_reason
            from public.runs
            where id = %s and worker_id = %s and status = 'running'
              and stop_requested_at is not null
            for update
            """,
            (run_id, worker_id),
        ).fetchone()
        if not run:
            return None

        # US-27.10: the stop was a cancellation. The run is retired here
        # rather than re-queued — a mis-dispatch that goes back to the pool is
        # the same mis-dispatch waiting to be claimed again.
        if run.get("cancel_reason"):
            summary = _record_cancel(
                conn, run, run["cancel_reason"], "the manager (cancelled)"
            )
            return {**summary, "cancelled": True}

        conn.execute(
            """
            update public.runs
            set status = 'queued', worker_id = null, claimed_at = null,
                claim_expires_at = null, last_heartbeat_at = null,
                started_at = null, branch_ref = null, pr_url = null,
                paused_at = null, stop_requested_at = null
            where id = %s
            """,
            (run_id,),
        )

        target = run["prev_issue_status"] or _RESET_FALLBACK_STATUS.get(run["kind"])
        # US-27.1: a stopped feature run leaves its stories where they were,
        # not in `queued` against a claim that has been handed back.
        for m in _run_member_rows(conn, str(run["id"])):
            conn.execute(
                "update public.issues set status = %s where id = %s",
                (m["prev_issue_status"] or _RESET_FALLBACK_STATUS["code"],
                 m["issue_id"]),
            )
        if run["issue_id"]:
            if target:
                conn.execute(
                    "update public.issues set status = %s where id = %s",
                    (target, run["issue_id"]),
                )
            conn.execute(
                """
                insert into public.issue_events (org_id, issue_id, type, payload)
                values (%s, %s, 'run-stopped', %s)
                """,
                (
                    run["org_id"],
                    run["issue_id"],
                    json.dumps(
                        {
                            "run_id": str(run["id"]),
                            "kind": run["kind"],
                            "reset_to_status": target,
                            "agent_note": (note or "").strip() or None,
                        }
                    ),
                ),
            )
        conn.commit()
        return {"kind": run["kind"], "reset_to_status": target}


class ResetBlocked(Exception):
    """US-15.17/US-68.1: a reset was refused because a merged/shipped item is
    in the target's subtree — the caller maps this to a 409."""


class ResetStageError(Exception):
    """US-68.1: an unusable stage/type combination (PRD on a non-feature, an
    unknown stage) or a 'coding' reset with no approved plan to land on — the
    caller maps this to a 400/409."""


_STAGE_ARTIFACT_KINDS: dict[str, tuple[str, ...]] = {
    "elaboration": ("elaboration", "plan", "test_plan"),
    "planning": ("plan", "test_plan"),
    "coding": (),
}


def _cancel_active_runs(conn, issue_ids: list[str], reason: str) -> int:
    """Cancel any queued/running run on these issues, in the DB only — no
    GitHub call is ever made from a reset. Distinct from the cooperative
    `cancel_run` path: a reset is a hard, manager-initiated stop, not a
    request the worker can finish out."""
    return conn.execute(
        """
        update public.runs
        set status = 'cancelled', cancel_reason = %s, cancelled_at = now(),
            worker_id = null, claimed_at = null, claim_expires_at = null,
            last_heartbeat_at = null, paused_at = null, stop_requested_at = null
        where issue_id = any(%s) and status in ('queued', 'running')
        """,
        (reason, issue_ids),
    ).rowcount


def _clear_code_pointers(conn, issue_ids: list[str]) -> None:
    """DB pointers only. The pushed branch and any PR stay exactly as they
    are on GitHub — us-68.1's whole point is that a reset never reaches out
    there."""
    conn.execute(
        """
        update public.runs
        set branch_ref = null, pr_url = null, diff = null
        where issue_id = any(%s)
        """,
        (issue_ids,),
    )


def _reset_targets_to_stage(
    conn,
    targets: list[dict[str, Any]],
    stage: str,
    destination_status: str | None,
    note: str | None,
    actor: str | None,
) -> dict[str, Any]:
    """Apply one of Elaboration/Planning/Dispatch-for-Coding to a set of
    issues that are all known non-merged/non-done. `targets` is the root
    issue itself (Story/Chore) or a Feature's non-abandoned children."""
    ids = [t["id"] for t in targets]
    if not ids:
        return {
            "reset_count": 0,
            "runs_cancelled": 0,
            "artifacts_deleted": 0,
            "target_status": destination_status,
        }

    if stage == "coding":
        approved_ids = {
            r["issue_id"]
            for r in conn.execute(
                """
                select issue_id from public.artifacts
                where issue_id = any(%s) and kind = 'plan' and status = 'approved'
                """,
                (ids,),
            ).fetchall()
        }
        if any(t["id"] not in approved_ids for t in targets):
            raise ResetStageError(
                "no approved plan exists for this item — reset to Planning instead"
            )
        target_status = "planned"
    elif stage == "planning":
        target_status = "ready"
    else:
        target_status = destination_status

    cancelled = _cancel_active_runs(
        conn, ids, f"reset to {stage} by the manager (us-68.1)"
    )
    _clear_code_pointers(conn, ids)

    kinds = _STAGE_ARTIFACT_KINDS[stage]
    deleted = 0
    if kinds:
        deleted = conn.execute(
            "delete from public.artifacts where issue_id = any(%s) and kind = any(%s)",
            (ids, list(kinds)),
        ).rowcount

    payload = json.dumps(
        {
            "stage": stage,
            "destination_status": target_status,
            "note": (note or "").strip() or None,
            "by": actor or "manager",
        }
    )
    conn.execute(
        """
        insert into public.issue_events (org_id, issue_id, type, payload)
        select org_id, id, 'issue-reset', %s::jsonb
        from public.issues where id = any(%s)
        """,
        (payload, ids),
    )

    conn.execute(
        "update public.issues set status = %s where id = any(%s)",
        (target_status, ids),
    )

    return {
        "reset_count": len(ids),
        "runs_cancelled": cancelled,
        "artifacts_deleted": deleted,
        "target_status": target_status,
    }


def _reset_feature_prd(
    conn, feature: dict[str, Any], note: str | None, actor: str | None
) -> dict[str, Any]:
    """A new PRD implies a new breakdown, so this abandons every non-abandoned
    child alongside deleting the PRD itself — the children a stale PRD wrote
    have nothing left to be children of."""
    children = conn.execute(
        """
        select id, status from public.issues
        where parent_id = %s and abandoned_at is null
        """,
        (feature["id"],),
    ).fetchall()
    if any(c["status"] in ("merged", "done") for c in children):
        raise ResetBlocked(
            "cannot reset: a merged or shipped story is in this subtree"
        )

    child_ids = [c["id"] for c in children]
    all_ids = [feature["id"]] + child_ids

    cancelled = _cancel_active_runs(
        conn, all_ids, "reset to prd by the manager (us-68.1)"
    )
    _clear_code_pointers(conn, all_ids)

    if child_ids:
        conn.execute(
            "update public.issues set abandoned_at = now() where id = any(%s)",
            (child_ids,),
        )

    deleted = conn.execute(
        "delete from public.artifacts where issue_id = %s and kind = 'prd'",
        (feature["id"],),
    ).rowcount

    payload = json.dumps(
        {
            "stage": "prd",
            "destination_status": "draft",
            "note": (note or "").strip() or None,
            "children_abandoned": len(child_ids),
            "by": actor or "manager",
        }
    )
    conn.execute(
        """
        insert into public.issue_events (org_id, issue_id, type, payload)
        values (%s, %s, 'issue-reset', %s::jsonb)
        """,
        (feature["org_id"], feature["id"], payload),
    )

    conn.execute(
        "update public.issues set status = 'draft' where id = %s",
        (feature["id"],),
    )

    return {
        "reset_count": 1,
        "children_abandoned": len(child_ids),
        "runs_cancelled": cancelled,
        "artifacts_deleted": deleted,
        "target_status": "draft",
    }


def reset_issue_to_stage(
    settings: Settings,
    root_issue_id: str,
    stage: str,
    destination_status: str | None = None,
    note: str | None = None,
    actor: str | None = None,
) -> dict[str, Any] | None:
    """US-68.1: send a work item back to a specific stage — PRD (Feature
    only), Elaboration, Planning, or Dispatch for Coding — instead of always
    wiping it to Triage the way US-15.17's full reset did. Discards only the
    artifacts that stage renders stale, cancels any active run in the
    database, and never touches GitHub: a pushed branch or open PR is left
    exactly as it is, unlike the full reset this replaces.

    A Feature reset to Elaboration/Planning/Dispatch-for-Coding cascades to
    every non-abandoned child story; the feature's own status and PRD are
    untouched by those three. Only the `prd` stage touches the feature
    itself, and it abandons the existing breakdown to do it.

    Raises ResetBlocked if the target issue, or (for a Feature) any
    non-abandoned descendant, is merged/done. Raises ResetStageError for an
    unknown stage, `prd` on a non-feature, or a `coding` reset with no
    approved plan to land on. Returns None if the root issue doesn't exist."""
    if stage not in ("prd", "elaboration", "planning", "coding"):
        raise ResetStageError(f"unknown stage: {stage}")
    if stage == "elaboration":
        destination_status = destination_status or "draft"
        if destination_status not in ("draft", "ready"):
            raise ResetStageError(
                "an elaboration reset must land on draft or ready"
            )

    with _connect(settings) as conn:
        root = conn.execute(
            "select id, status, type, org_id from public.issues where id = %s",
            (root_issue_id,),
        ).fetchone()
        if not root:
            return None
        if root["status"] in ("merged", "done"):
            raise ResetBlocked("cannot reset: this item is merged or shipped")

        if stage == "prd":
            if root["type"] != "feature":
                raise ResetStageError("only a feature has a PRD to reset")
            summary = _reset_feature_prd(conn, root, note, actor)
            conn.commit()
            return summary

        if root["type"] == "feature":
            targets = conn.execute(
                """
                select id, status from public.issues
                where parent_id = %s and abandoned_at is null
                """,
                (root["id"],),
            ).fetchall()
        else:
            targets = [root]

        if any(t["status"] in ("merged", "done") for t in targets):
            raise ResetBlocked(
                "cannot reset: a merged or shipped item is in this subtree"
            )

        summary = _reset_targets_to_stage(
            conn, targets, stage, destination_status, note, actor
        )
        conn.commit()
        return summary


def extend_claim(
    settings: Settings,
    run_id: str,
    worker_id: str,
    tool: str | None = None,
) -> dict[str, Any] | None:
    """Heartbeat: extend the caller's live claim by its worker-type lease.
    Returns the row with the new claim_expires_at (truthy), or None when
    there's no live claim to extend.

    US-14.8: `tool` names the MCP call that caused the heartbeat, and is
    recorded so the manager can be told what the agent is doing rather
    than only that it made some call at some time. The intercept already
    existed for the lease — this keeps the account instead of discarding
    it. Recording never affects the heartbeat: a failure here must not
    cost a worker its claim, so it is swallowed.

    US-57.16: the fallback (when `runner_config.max_run_minutes` is unset)
    used to be a SQL literal baked into this function — '15 minutes' for an
    autonomous worker — independent of, and silently shorter than, the
    120-minute default `claim_run` uses from `_LEASES`. A run's first claim
    got 120 minutes; its first heartbeat quietly shrank that to 15, and every
    heartbeat after held it there. Reading `_LEASES` here too makes this the
    one place that default lives — the superadmin still overrides it for
    everyone via `/admin/run-config`'s "Minutes per story"
    (`platform_run_config.max_run_minutes`, cascaded into every agent's own
    `runner_config.max_run_minutes`), which this coalesce still reads first."""
    if not _valid_uuid(run_id):
        return None
    with _connect(settings) as conn:
        worker = conn.execute(
            "select type from public.workers where id = %s", (worker_id,)
        ).fetchone()
        lease = _LEASES.get((worker or {}).get("type"), _LEASES["autonomous"])
        row = conn.execute(
            """
            update public.runs r
            set claim_expires_at = now() + coalesce(
              -- US-31.2: honor the configured lease here too — otherwise the
              -- first heartbeat would silently shrink a 60-minute claim back
              -- to the type default.
              (select (rc.max_run_minutes || ' minutes')::interval
                 from public.runner_config rc
                where rc.worker_id = r.worker_id),
              %s::interval),
                last_heartbeat_at = now()
            where r.id = %s and r.worker_id = %s
              and r.status = 'running'
            returning r.id, r.claim_expires_at
            """,
            (lease, run_id, worker_id),
        ).fetchone()
        if row and tool:
            try:
                conn.execute(
                    "select public.record_run_activity(%s, %s)",
                    (run_id, tool),
                )
            except Exception:  # noqa: BLE001 - narration is never load-bearing
                logger.exception("run activity not recorded for %s", run_id)
        conn.commit()
    return row


def record_run_activity(settings: Settings, run_id: str, tool: str) -> None:
    """US-14.8: record a tool call outside the heartbeat path (claim, where
    there is no lease to extend yet). Never load-bearing."""
    if not _valid_uuid(run_id):
        return
    try:
        with _connect(settings) as conn:
            conn.execute(
                "select public.record_run_activity(%s, %s)", (run_id, tool)
            )
            conn.commit()
    except Exception:  # noqa: BLE001 - narration is never load-bearing
        logger.exception("run activity not recorded for %s", run_id)


def get_run_activity(
    settings: Settings, run_id: str, limit: int = 40
) -> list[dict[str, Any]]:
    """US-14.8: the run's tool trace, newest first."""
    if not _valid_uuid(run_id):
        return []
    with _connect(settings) as conn:
        return conn.execute(
            "select tool, at from public.run_activity "
            "where run_id = %s order by at desc limit %s",
            (run_id, limit),
        ).fetchall()


def record_progress_note(
    settings: Settings, run: dict[str, Any], worker: dict[str, Any], note: str
) -> None:
    """US-5.2: a heartbeat's note lands on the issue timeline (type
    'progress-note', worker named in the payload) — the Workers page's
    current-activity view reads it from there. US-13.12: issue-less
    (project-scoped) runs have no timeline to note on."""
    if not run.get("issue_id"):
        return
    with _connect(settings) as conn:
        conn.execute(
            """
            insert into public.issue_events (org_id, issue_id, type, payload)
            values (%s, %s, 'progress-note', %s)
            """,
            (
                str(run["org_id"]),
                str(run["issue_id"]),
                json.dumps(
                    {
                        "run_id": str(run["id"]),
                        "kind": run["kind"],
                        "worker": worker["name"],
                        "note": note,
                    }
                ),
            ),
        )
        conn.commit()


def set_run_handback_notes(
    settings: Settings, run_id: str, notes: str
) -> None:
    """US-13.3: the agent's hand-back notes, stored on the run so the
    review surface shows them at the gate."""
    if not _valid_uuid(run_id):
        return
    with _connect(settings) as conn:
        conn.execute(
            "update public.runs set handback_notes = %s where id = %s",
            (notes, run_id),
        )
        conn.commit()


def release_claim(
    settings: Settings,
    run_id: str,
    worker: dict[str, Any],
    note: str | None = None,
) -> bool:
    """Voluntary hand-back: run and issue return to the pool with a note."""
    if not _valid_uuid(run_id):
        return False
    with _connect(settings) as conn:
        run = conn.execute(
            """
            update public.runs
            set status = 'queued', worker_id = null, claimed_at = null,
                claim_expires_at = null, last_heartbeat_at = null
            where id = %s and worker_id = %s and status = 'running'
            returning id, org_id, issue_id, kind, pushed_head_sha
            """,
            (run_id, worker["id"]),
        ).fetchone()
        if not run:
            return False
        # US-13.6: only plan/code claims moved the issue's status in the
        # first place — releasing a prd/breakdown (or future issue-less)
        # claim must not knock the issue to 'queued'.
        if run["kind"] in ("plan", "code"):
            conn.execute(
                # US-27.1: run_issue_ids covers both shapes — `where id = %s`
                # alone left a feature run's member stories reading as
                # claimed by a claim that no longer exists.
                "update public.issues set status = 'queued' "
                "where id in (select issue_id from public.run_issue_ids(%s))",
                (run["id"],),
            )
        payload: dict[str, Any] = {
            "run_id": str(run["id"]),
            "kind": run["kind"],
            "worker": worker["name"],
            "note": note,
        }
        if run.get("pushed_head_sha"):
            # release-after-push: the next claimer continues from this branch
            payload["pushed_head_sha"] = run["pushed_head_sha"]
        if run["issue_id"]:
            conn.execute(
                """
                insert into public.issue_events (org_id, issue_id, type, payload)
                values (%s, %s, 'run-released', %s)
                """,
                (run["org_id"], run["issue_id"], json.dumps(payload)),
            )
        conn.commit()
        return True


# ---------------------------------------------------------------------------
# Phase 59: a run pauses instead of failing, and resumes instead of
# restarting (us-59.1 through us-59.9).
# ---------------------------------------------------------------------------

DEFAULT_MAX_RESUME_ATTEMPTS = 3
DEFAULT_MAX_CLARIFICATION_ROUNDS = 3
# us-59.8: long for a question (waiting on a slow human must never be
# punished — that is the entire point of us-59.5), short for a turn-limit
# pause whose auto-resume already gave up (nothing further is pending, the
# run is simply unclaimed).
DEFAULT_AWAITING_INPUT_TTL_HOURS = 14 * 24
DEFAULT_PAUSED_TTL_HOURS = 48


def _resume_policy(settings: Settings, worker_id: str | None) -> dict[str, Any]:
    """us-59.3/59.5/59.8: the autonomy_policy knobs this phase reads, with
    defaults — read server-side because the server, not the runner, decides
    whether another resume is offered."""
    policy: dict[str, Any] = {}
    if worker_id:
        config = get_runner_config(settings, worker_id)
        policy = (config or {}).get("autonomy_policy") or {}
    return {
        "max_resume_attempts": int(
            policy.get("max_resume_attempts", DEFAULT_MAX_RESUME_ATTEMPTS)
        ),
        "max_clarification_rounds": int(
            policy.get(
                "max_clarification_rounds", DEFAULT_MAX_CLARIFICATION_ROUNDS
            )
        ),
        "awaiting_input_ttl_hours": int(
            policy.get(
                "awaiting_input_ttl_hours", DEFAULT_AWAITING_INPUT_TTL_HOURS
            )
        ),
        "paused_ttl_hours": int(
            policy.get("paused_ttl_hours", DEFAULT_PAUSED_TTL_HOURS)
        ),
    }


def pause_run(
    settings: Settings,
    run_id: str,
    *,
    reason: str,
    claude_session_id: str | None,
    stdout: str | None,
    error: str | None,
    worker_name: str | None = None,
) -> tuple[bool, str, int, int]:
    """US-59.3: a turn-limit hit lands here instead of `complete_run`'s
    failed path — it is a work-progress signal, not a fault. The claim
    releases immediately (worker_id stays, so the owning worker can resume
    it — us-59.3's worker-affinity decision — but claimed_at/claim_expires_at
    clear so it does not hold that worker hostage while parked, us-59.9's
    starting point). Bounded by `autonomy_policy.max_resume_attempts`; past
    the cap this becomes a real failure instead of pausing again, so nothing
    loops forever. Returns (accepted, landed_status, attempts_used, cap) —
    the numbers ride along so a caller that falls through to an ordinary
    failure can say why in the manager's language without a second query."""
    with _connect(settings) as conn:
        run = conn.execute(
            """
            select id, org_id, issue_id, kind, worker_id, resume_attempts
            from public.runs where id = %s and status = 'running'
            """,
            (run_id,),
        ).fetchone()
    if not run:
        return False, "", 0, 0
    policy = _resume_policy(
        settings, str(run["worker_id"]) if run.get("worker_id") else None
    )
    attempts = int(run.get("resume_attempts") or 0)
    cap = policy["max_resume_attempts"]
    if attempts >= cap:
        # US-27.12 in spirit: say why, with the numbers — but leave landing
        # it `failed` to the caller's own ordinary failure path, which
        # already owns fault-class recording, issue sync and the incident
        # feed. Calling `complete_run` here too would just race that one and
        # lose (its own `status = 'running'` guard would already be gone).
        return False, "exhausted", attempts, cap
    with _connect(settings) as conn:
        conn.execute(
            """
            update public.runs
            set status = 'paused', resume_reason = %s, stdout = %s,
                claude_session_id = coalesce(%s, claude_session_id),
                resume_attempts = resume_attempts + 1, resume_state_at = now(),
                claimed_at = null, claim_expires_at = null, last_heartbeat_at = null
            where id = %s
            """,
            (reason, stdout, claude_session_id, run_id),
        )
        if run["issue_id"]:
            conn.execute(
                """
                insert into public.issue_events (org_id, issue_id, type, payload)
                values (%s, %s, 'run-paused', %s)
                """,
                (
                    run["org_id"],
                    run["issue_id"],
                    json.dumps(
                        {
                            "run_id": str(run_id),
                            "kind": run["kind"],
                            "worker": worker_name,
                            "reason": reason,
                            "attempt": attempts + 1,
                            "cap": cap,
                        }
                    ),
                ),
            )
        conn.commit()
    return True, "paused", attempts + 1, cap


def clarification_round_count(settings: Settings, run_id: str) -> tuple[int, int]:
    """US-59.5: how many clarification round-trips this run has spent, and
    the cap — read before `request_clarification` asks another, so a refusal
    can name both numbers."""
    if not _valid_uuid(run_id):
        return 0, DEFAULT_MAX_CLARIFICATION_ROUNDS
    with _connect(settings) as conn:
        row = conn.execute(
            "select clarification_rounds, worker_id from public.runs where id = %s",
            (run_id,),
        ).fetchone()
    if not row:
        return 0, DEFAULT_MAX_CLARIFICATION_ROUNDS
    policy = _resume_policy(
        settings, str(row["worker_id"]) if row.get("worker_id") else None
    )
    return int(row.get("clarification_rounds") or 0), policy["max_clarification_rounds"]


def record_clarification_round(settings: Settings, run_id: str) -> None:
    """US-59.5: one more round spent — called when a question is actually
    asked, not when it is merely requested, so a refused request never
    counts against the cap."""
    if not _valid_uuid(run_id):
        return
    with _connect(settings) as conn:
        conn.execute(
            "update public.runs set clarification_rounds = clarification_rounds + 1 "
            "where id = %s",
            (run_id,),
        )
        conn.commit()


def has_pending_clarification(settings: Settings, run: dict[str, Any]) -> bool:
    """US-59.5: whether this run's work item is carrying an unanswered
    question — checked at submit time so a run that asked and then stopped
    (for any reason) lands `awaiting_input` instead of `failed`/`paused`."""
    if not run.get("issue_id"):
        return False
    with _connect(settings) as conn:
        row = conn.execute(
            """
            select 1 from public.clarifications
            where issue_id = %s and org_id = %s
              and answer is null and selected_options is null
            limit 1
            """,
            (str(run["issue_id"]), str(run["org_id"])),
        ).fetchone()
    return bool(row)


def awaiting_input_run(
    settings: Settings,
    run_id: str,
    *,
    claude_session_id: str | None,
    stdout: str | None,
    worker_name: str | None = None,
) -> bool:
    """US-59.5: the run parked on a question instead of racing its own turn
    budget to poll for an answer. Claim releases exactly as `pause_run`'s
    does, and for the same reason — a slow human answer must not tie up the
    worker that asked."""
    with _connect(settings) as conn:
        run = conn.execute(
            """
            select id, org_id, issue_id, kind, worker_id
            from public.runs where id = %s and status = 'running'
            """,
            (run_id,),
        ).fetchone()
        if not run:
            return False
        conn.execute(
            """
            update public.runs
            set status = 'awaiting_input', resume_reason = 'clarification',
                stdout = %s, claude_session_id = coalesce(%s, claude_session_id),
                resume_state_at = now(),
                claimed_at = null, claim_expires_at = null, last_heartbeat_at = null
            where id = %s
            """,
            (stdout, claude_session_id, run_id),
        )
        if run["issue_id"]:
            conn.execute(
                """
                insert into public.issue_events (org_id, issue_id, type, payload)
                values (%s, %s, 'run-awaiting-input', %s)
                """,
                (
                    run["org_id"],
                    run["issue_id"],
                    json.dumps(
                        {
                            "run_id": str(run_id),
                            "kind": run["kind"],
                            "worker": worker_name,
                        }
                    ),
                ),
            )
        conn.commit()
    return True


def resume_claim(
    settings: Settings, run_id: str, worker: dict[str, Any]
) -> dict[str, Any] | None:
    """US-59.3/59.4/59.9: continue a paused/awaiting_input run — atomically,
    the same guarantee `claim_run` gives a fresh claim. Scoped to
    `worker_id = this worker` (the affinity us-59.3/59.6 decided on: only the
    machine that holds the matching local transcript and workspace can
    resume it, v1 has no cross-machine story). Two triggers racing to resume
    the same run (an auto-resume sweep and a reconnecting worker, say) settle
    here for free — Postgres's row lock on the UPDATE lets exactly one win;
    the loser's WHERE clause simply matches nothing once the first commits,
    which is us-59.3's single-flight gate without a second table to invent."""
    if not _valid_uuid(run_id):
        return None
    lease = _LEASES.get(worker["type"], _LEASES["autonomous"])
    with _connect(settings) as conn:
        run = conn.execute(
            """
            update public.runs
            set status = 'running', claimed_at = now(),
                started_at = coalesce(started_at, now()),
                claim_expires_at = now() + least(
                  coalesce(
                    (select (rc.max_run_minutes || ' minutes')::interval
                       from public.runner_config rc
                      where rc.worker_id = %s),
                    %s::interval
                  ) * public.run_work_units(runs.id),
                  coalesce(
                    (select (rc.max_total_run_minutes || ' minutes')::interval
                       from public.runner_config rc
                      where rc.worker_id = %s),
                    %s::interval
                  )
                ),
                last_heartbeat_at = now()
            where id = %s and org_id = %s and worker_id = %s
              and status in ('paused', 'awaiting_input')
            returning id, org_id, issue_id, kind, claim_expires_at,
                      claude_session_id, resume_reason
            """,
            (
                worker["id"],
                lease,
                worker["id"],
                f"{DEFAULT_TOTAL_RUN_MINUTES} minutes",
                run_id,
                worker["org_id"],
                worker["id"],
            ),
        ).fetchone()
        if not run:
            return None
        if run["issue_id"]:
            conn.execute(
                """
                insert into public.issue_events (org_id, issue_id, type, payload)
                values (%s, %s, 'run-resumed', %s)
                """,
                (
                    run["org_id"],
                    run["issue_id"],
                    json.dumps(
                        {
                            "run_id": str(run["id"]),
                            "kind": run["kind"],
                            "worker": worker["name"],
                            "resume_reason": run["resume_reason"],
                        }
                    ),
                ),
            )
        conn.commit()
        return run


def list_worker_resumable(
    settings: Settings, worker: dict[str, Any]
) -> list[dict[str, Any]]:
    """US-59.9: this worker's OWN paused/awaiting_input runs — checked before
    it polls the ordinary pool, so a worker that is free resumes its own
    parked work before it goes looking for something new. Excludes
    awaiting_input runs still genuinely unanswered — resuming those before an
    answer exists would just park them again."""
    with _connect(settings) as conn:
        return conn.execute(
            """
            select r.id, r.kind, r.issue_id, r.status, r.resume_reason,
                   i.title as issue_title
            from public.runs r
            left join public.issues i on i.id = r.issue_id
            where r.org_id = %(org)s and r.worker_id = %(worker)s
              and (
                r.status = 'paused'
                or (
                  r.status = 'awaiting_input'
                  and not exists (
                    select 1 from public.clarifications c
                    where c.issue_id = r.issue_id
                      and c.answer is null and c.selected_options is null
                  )
                )
              )
            order by r.resume_state_at asc
            """,
            {"org": str(worker["org_id"]), "worker": str(worker["id"])},
        ).fetchall()


def mark_stopped_resumable(
    settings: Settings, run_id: str, org_id: str, member: dict[str, Any] | None
) -> bool:
    """US-59.3: the manager's explicit "resume" action on a spend-ceiling
    `stopped` run. Deliberately manual — resuming past a ceiling must never
    be silent, or the ceiling stops meaning anything. Requires a session id:
    a `stopped` run from before Phase 59 (or one whose CLI never got far
    enough to report one) has nothing to resume into."""
    if not _valid_uuid(run_id):
        return False
    with _connect(settings) as conn:
        row = conn.execute(
            """
            update public.runs
            set status = 'paused', resume_reason = 'manager-approved',
                resume_state_at = now()
            where id = %s and org_id = %s and status = 'stopped'
              and worker_id is not null and claude_session_id is not null
            returning id, issue_id
            """,
            (run_id, org_id),
        ).fetchone()
        if not row:
            return False
        if row["issue_id"]:
            conn.execute(
                """
                insert into public.issue_events (org_id, issue_id, type, payload)
                values (%s, %s, 'run-resume-approved', %s)
                """,
                (
                    org_id,
                    row["issue_id"],
                    json.dumps(
                        {
                            "run_id": str(run_id),
                            "member": (member or {}).get("name"),
                        }
                    ),
                ),
            )
        conn.commit()
    return True


def list_resumable_runs(settings: Settings, org_id: str) -> list[dict[str, Any]]:
    """US-59.7: every run parked in a resumable state, for the manager's own
    surface — split by the caller into a "needs your input" tier
    (`awaiting_input`) and an informational one (`paused`, resumable
    `stopped`), per that story's decision that the two must never read as
    one flat list."""
    with _connect(settings) as conn:
        return conn.execute(
            """
            select r.id, r.kind, r.status, r.issue_id, r.resume_reason,
                   r.resume_state_at, r.resume_attempts, r.stopped_reason,
                   i.title as issue_title, p.name as project_name,
                   w.name as worker_name,
                   (
                     select c.question from public.clarifications c
                     where c.issue_id = r.issue_id
                       and c.answer is null and c.selected_options is null
                     order by c.asked_at desc limit 1
                   ) as pending_question
            from public.runs r
            left join public.issues i on i.id = r.issue_id
            join public.projects p on p.id = r.project_id
            left join public.workers w on w.id = r.worker_id
            where r.org_id = %s
              and (
                r.status in ('paused', 'awaiting_input')
                or (r.status = 'stopped' and r.claude_session_id is not null)
              )
            order by r.resume_state_at asc nulls last, r.created_at asc
            """,
            (org_id,),
        ).fetchall()


def abandon_run(
    settings: Settings,
    run_id: str,
    org_id: str,
    *,
    reason: str,
    member: dict[str, Any] | None = None,
) -> bool:
    """US-59.7/59.8: close out a parked run on purpose — a manager's click or
    us-59.8's TTL sweep land here identically, so there is exactly one path
    for "we're done with this", never two that could disagree. Scoped to
    `status in ('paused', 'awaiting_input')`: a run mid-resume has already
    left that status by the time this runs (it is 'running' again), so the
    same guard that protects every other terminal transition also protects
    this one from tearing down a workspace a live process still has open —
    the WHERE clause simply matches nothing and the caller sees `False`."""
    if not _valid_uuid(run_id):
        return False
    with _connect(settings) as conn:
        row = conn.execute(
            """
            update public.runs
            set status = 'abandoned', abandon_reason = %s, abandoned_at = now(),
                abandoned_by = %s, worker_id = null, resume_state_at = null
            where id = %s and org_id = %s
              and status in ('paused', 'awaiting_input')
            returning id, issue_id, kind
            """,
            (reason, str(member["id"]) if member else None, run_id, org_id),
        ).fetchone()
        if not row:
            return False
        if row["issue_id"]:
            conn.execute(
                """
                insert into public.issue_events (org_id, issue_id, type, payload)
                values (%s, %s, 'run-abandoned', %s)
                """,
                (
                    org_id,
                    row["issue_id"],
                    json.dumps(
                        {
                            "run_id": str(run_id),
                            "kind": row["kind"],
                            "reason": reason,
                            "member": (member or {}).get("name"),
                        }
                    ),
                ),
            )
        conn.commit()
    return True


def sweep_unattended_resumable(settings: Settings) -> int:
    """US-59.8: a parked run nobody ever answers or retries closes itself
    out — through `abandon_run`'s exact path, on a per-status TTL (a slow
    human answer is never punished; an exhausted, unclaimed pause is). Meant
    to ride the same periodic cadence as the runner/API's other sweeps
    (`requeue_stale_heartbeats`, the pool-placement sweep)."""
    with _connect(settings) as conn:
        rows = conn.execute(
            """
            select r.id, r.org_id
            from public.runs r
            join public.runner_config rc on rc.worker_id = r.worker_id
            where r.status in ('paused', 'awaiting_input')
              and r.resume_state_at < now() - make_interval(
                hours => case
                  when r.status = 'awaiting_input'
                    then coalesce(
                      (rc.autonomy_policy->>'awaiting_input_ttl_hours')::int,
                      %s
                    )
                  else coalesce(
                    (rc.autonomy_policy->>'paused_ttl_hours')::int, %s
                  )
                end
              )
            """,
            (DEFAULT_AWAITING_INPUT_TTL_HOURS, DEFAULT_PAUSED_TTL_HOURS),
        ).fetchall()
    swept = 0
    for row in rows:
        if abandon_run(
            settings,
            str(row["id"]),
            str(row["org_id"]),
            reason="unattended past its TTL",
        ):
            swept += 1
    return swept


# US-31.2: how long a supervisor-managed agent may go silent before its claim
# is presumed dead. The runner heartbeats every 20 seconds while it holds a
# run, so 90 seconds is four missed beats — "noticed in about a minute", per
# the story, instead of at the end of whatever lease the manager set. Applies
# ONLY to workers with a runner_config row (the supervisor fleet): external
# MCP workers heartbeat through tool calls at their own pace and keep the
# lease as their only reclaim.
HEARTBEAT_STALE_SECONDS = 90


def requeue_stale_heartbeats(settings: Settings) -> int:
    """US-31.2: a claim whose agent has stopped reporting is requeued inside
    the lease, with its own event note — a manager must be able to tell a
    slow agent from a dead one. On 2026-07-26 two runs stopped heartbeating
    three seconds after being claimed and sat out the full fifteen minutes;
    `last_heartbeat_at` was written on every beat and read by nothing.

    US-59.4: a stale run carrying a `claude_session_id` has something to
    reattach to — it lands `paused` (worker_id kept, so only that worker's
    resume-claim can reach it) instead of `queued` for anyone. A worker that
    reconnects resumes it through the ordinary us-59.9 priority check; one
    that never comes back leaves it exactly where us-59.7 surfaces it and
    us-59.8's TTL eventually closes out — the "grace window" this story
    describes, expressed as the same parked state every other pause uses
    rather than a bespoke waiting room. A stale run with NO session id has
    nothing to reattach to and takes today's path unchanged."""
    with _connect(settings) as conn:
        rows = conn.execute(
            """
            with stale as (
              select r.id, r.worker_id, r.claimed_at, r.claude_session_id,
                     round(extract(epoch from (
                       now() - coalesce(r.last_heartbeat_at, r.claimed_at))))::int
                       as silent_seconds
              from public.runs r
              join public.runner_config rc on rc.worker_id = r.worker_id
              where r.status = 'running' and r.worker_id is not null
                and r.pushed_head_sha is null
                and coalesce(r.last_heartbeat_at, r.claimed_at)
                      < now() - make_interval(secs => %s)
              for update of r skip locked
            )
            update public.runs r
            set status = case when s.claude_session_id is not null
                              then 'paused' else 'queued' end,
                resume_reason = case when s.claude_session_id is not null
                                     then 'worker-unresponsive' else null end,
                resume_state_at = case when s.claude_session_id is not null
                                       then now() else null end,
                worker_id = case when s.claude_session_id is not null
                                 then r.worker_id else null end,
                claimed_at = null, claim_expires_at = null, last_heartbeat_at = null
            from stale s
            left join public.workers w on w.id = s.worker_id
            where r.id = s.id
            returning r.id, r.org_id, r.issue_id, r.kind, r.status,
                      coalesce(w.name, '') as worker_name, s.silent_seconds,
                      s.worker_id
            """,
            (HEARTBEAT_STALE_SECONDS,),
        ).fetchall()
        for row in rows:
            resumed = row["status"] == "paused"
            # US-79.8: both landings are the same death — the agent stopped
            # reporting. The parked one is marked resumable so the console
            # can tell "waiting for its worker" from "requeued for anyone".
            record_agent_failure(
                conn,
                str(row["id"]),
                "heartbeat-stale",
                worker_id=str(row["worker_id"]) if row.get("worker_id") else None,
                error=(
                    "agent stopped reporting — parked for its worker to "
                    "resume on reconnect"
                    if resumed
                    else "agent stopped reporting — requeued for another worker"
                ),
                detail={"silent_seconds": row["silent_seconds"]},
                resumable=resumed,
            )
            if not resumed:
                # US-31.5: going silent consumes an attempt for that agent —
                # one of the two requeue paths that leave no trace in `runs`.
                # A parked (resumable) landing consumes nothing: the run is
                # not done, it is waiting its turn — us-59.3's same rule.
                record_run_attempt(
                    conn,
                    str(row["org_id"]),
                    str(row["issue_id"]) if row["issue_id"] else None,
                    str(row["id"]),
                    str(row["worker_id"]) if row.get("worker_id") else None,
                    row["kind"],
                    "heartbeat-stale",
                )
                # Same guard as the lease sweep: only plan/code claims moved
                # the issue, so only they move it back. A paused landing
                # leaves the issue exactly where it is — the work is not
                # abandoned, only waiting for its worker to reconnect.
                if row["kind"] in ("plan", "code"):
                    conn.execute(
                        "update public.issues set status = 'queued' "
                        "where id in (select issue_id from public.run_issue_ids(%s))",
                        (row["id"],),
                    )
            if row["issue_id"]:
                conn.execute(
                    """
                    insert into public.issue_events (org_id, issue_id, type, payload)
                    values (%s, %s, %s, %s)
                    """,
                    (
                        row["org_id"],
                        row["issue_id"],
                        "run-paused" if resumed else "claim-expired",
                        json.dumps(
                            {
                                "run_id": str(row["id"]),
                                "kind": row["kind"],
                                "worker": row["worker_name"],
                                "silent_seconds": row["silent_seconds"],
                                # Deliberately NOT the lease-expiry wording:
                                # the manager needs to tell a slow agent
                                # apart from one that has stopped.
                                "note": (
                                    "agent stopped reporting — parked for "
                                    "that worker to resume on reconnect"
                                    if resumed
                                    else "agent stopped reporting — requeued "
                                    "for another worker"
                                ),
                            }
                        ),
                    ),
                )
        conn.commit()
        return len(rows)


def requeue_expired_claims(settings: Settings) -> int:
    """Expired claims return to the pool (running → queued) — abandoned
    work is retryable, not dead (US-3.2). Runs at startup and before
    every pool listing. Expired claims WITH pushed work are the
    reconciler's business (US-3.4 auto-submit) and are skipped here.
    US-31.2: the stale-heartbeat sweep rides along, so every caller that
    reclaims lease-expired work also notices dead agents."""
    requeue_stale_heartbeats(settings)
    with _connect(settings) as conn:
        rows = conn.execute(
            """
            with expired as (
              select r.id, r.worker_id, r.claimed_at
              from public.runs r
              where r.status = 'running' and r.worker_id is not null
                and r.claim_expires_at < now()
                and r.pushed_head_sha is null
              for update skip locked
            )
            update public.runs r
            set status = 'queued', worker_id = null, claimed_at = null,
                claim_expires_at = null, last_heartbeat_at = null
            from expired e
            left join public.workers w on w.id = e.worker_id
            where r.id = e.id
            returning r.id, r.org_id, r.issue_id, r.kind,
                      coalesce(w.name, '') as worker_name, e.worker_id,
                      round(extract(epoch from (now() - e.claimed_at)) / 60)::int
                        as held_minutes
            """,
        ).fetchall()
        for row in rows:
            # US-31.5: a lease that expires without a submission consumes an
            # attempt. This is the path that produced the 2026-07-26 loop and
            # left ZERO failed runs behind to count.
            record_run_attempt(
                conn,
                str(row["org_id"]),
                str(row["issue_id"]) if row["issue_id"] else None,
                str(row["id"]),
                str(row["worker_id"]) if row.get("worker_id") else None,
                row["kind"],
                "lease-expired",
            )
            # US-79.8: the agent died holding the claim — the one failure
            # mode that used to leave nothing but this sweep's side effects.
            record_agent_failure(
                conn,
                str(row["id"]),
                "lease-expired",
                worker_id=str(row["worker_id"]) if row.get("worker_id") else None,
                error=(
                    "lease expired without a submission — the agent died "
                    "holding the claim"
                ),
                detail={"held_minutes": row["held_minutes"]},
            )
            # US-13.6: only plan/code claims advance the issue — same guard
            # as release_claim, closing the documented prd/breakdown gap.
            if row["kind"] in ("plan", "code"):
                conn.execute(
                    # US-27.1: every story the expired claim covered.
                    "update public.issues set status = 'queued' "
                    "where id in (select issue_id from public.run_issue_ids(%s))",
                    (row["id"],),
                )
            if row["issue_id"]:
                conn.execute(
                    """
                    insert into public.issue_events (org_id, issue_id, type, payload)
                    values (%s, %s, 'claim-expired', %s)
                    """,
                    (
                        row["org_id"],
                        row["issue_id"],
                        json.dumps(
                            {
                                "run_id": str(row["id"]),
                                "kind": row["kind"],
                                "worker": row["worker_name"],
                                "held_minutes": row["held_minutes"],
                                "note": (
                                    "lease expired without a submission — "
                                    "requeued for another worker"
                                ),
                            }
                        ),
                    ),
                )
        conn.commit()
        return len(rows)


def get_github_connections(settings: Settings, org_id: str) -> list[dict[str, Any]]:
    """All of an org's GitHub connections for the resolver (US-3.15).
    'app' sorts before 'pat', matching the preference order."""
    with _connect(settings) as conn:
        rows = conn.execute(
            "select id, method, installation_id, vault_secret_id, repos "
            "from public.github_connections where org_id = %s "
            "order by method asc, created_at asc",
            (org_id,),
        ).fetchall()
    return list(rows)


# ------------------------------------------- factory git remote (US-3.8)


# ------------------------------------------------- US-17.4: auto-approve


def get_project_auto_flags(settings: Settings, project_id: str) -> dict[str, Any]:
    """The project's build mode + the three auto-approve switches. Missing /
    unknown project reads as all-off (today's behaviour)."""
    if not _valid_uuid(project_id):
        return {"build_mode": "story", "prd": False, "plan": False, "code": False}
    with _connect(settings) as conn:
        row = conn.execute(
            "select build_mode, auto_approve_prd, auto_approve_plan, "
            "auto_approve_code from public.projects where id = %s",
            (project_id,),
        ).fetchone()
    if not row:
        return {"build_mode": "story", "prd": False, "plan": False, "code": False}
    return {
        "build_mode": row["build_mode"],
        "prd": bool(row["auto_approve_prd"]),
        "plan": bool(row["auto_approve_plan"]),
        "code": bool(row["auto_approve_code"]),
    }


def auto_approve_prd(settings: Settings, issue_id: str) -> dict[str, Any]:
    """US-17.4: approve the feature's draft PRD via the setting (no human) and
    auto-dispatch the breakdown. Returns {gate, dispatched_run}."""
    with _connect(settings) as conn:
        row = conn.execute(
            "select public.auto_approve_prd(%s) as run", (issue_id,)
        ).fetchone()
        conn.commit()
    return {"gate": "prd", "dispatched_run": str(row["run"]) if row and row["run"] else None}


def auto_approve_plan(settings: Settings, issue_id: str) -> dict[str, Any]:
    """US-17.4: approve the story's draft plan/test-plan via the setting and
    auto-dispatch the code run (which the build mode may hold). Test cases are
    materialised separately by the caller. Returns {gate, dispatched_run}."""
    with _connect(settings) as conn:
        row = conn.execute(
            "select public.auto_approve_plan(%s) as run", (issue_id,)
        ).fetchone()
        conn.commit()
    return {"gate": "plan", "dispatched_run": str(row["run"]) if row and row["run"] else None}


def auto_approve_code(settings: Settings, run_id: str) -> None:
    """US-17.4: record the code-review approval + merge effects (the caller has
    already merged the PR on GitHub), attributed to the auto-approve setting."""
    with _connect(settings) as conn:
        conn.execute("select public.auto_approve_code(%s)", (run_id,))
        conn.commit()


def set_run_merge_sha(settings: Settings, run_id: str, sha: str) -> None:
    """US-17.4: stamp the squash-merge commit before auto_approve_code reads it
    (mirrors the manual approve path's traceability write)."""
    with _connect(settings) as conn:
        conn.execute(
            "update public.runs set merge_commit_sha = %s where id = %s",
            (sha, run_id),
        )
        conn.commit()


def store_spec_map(
    settings: Settings,
    run_id: str,
    worker: dict[str, Any],
    entries: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """US-81.5: hold the case→spec linkage a code run reports at submit.

    Held on the run, applied at merge (apply_spec_map) — a rejected changeset
    must not leave cases claiming automation by specs that never landed.
    Scoping mirrors report_test_results: the run must be the worker's, cases
    must belong to a story the run covers, suites to the run's project.
    Returns None when the run isn't visible to the worker's org."""
    import uuid as _uuid

    try:
        _uuid.UUID(run_id)
    except ValueError:
        return None
    with _connect(settings) as conn:
        run = conn.execute(
            """
            select r.id, r.org_id, r.worker_id, r.status, i.project_id
            from public.runs r
            join public.issues i on i.id = r.issue_id
            where r.id = %s and r.org_id = %s
            """,
            (run_id, worker["org_id"]),
        ).fetchone()
        if not run:
            return None
        if str(run["worker_id"] or "") != str(worker["id"]) or run[
            "status"
        ] not in ("running", "succeeded"):
            return {
                "error": (
                    "only the claim holder (or the submitter while the run "
                    "is in review) can report a spec map for this run"
                )
            }
        # Project-scoped, not issue-scoped: a conversion chore (US-82.2)
        # legitimately automates cases belonging to other stories. Release
        # copies are excluded — they are frozen history.
        known_cases = {
            str(r["id"])
            for r in conn.execute(
                "select id from public.test_cases "
                "where project_id = %s and release_id is null",
                (run["project_id"],),
            ).fetchall()
        }
        unknown = sorted(
            {str(e.get("test_case_id")) for e in entries}
            - known_cases
        )
        if unknown:
            return {
                "error": "unknown test_case_id(s): " + ", ".join(unknown[:5])
            }
        known_suites = {
            str(r["id"])
            for r in conn.execute(
                "select id from public.test_suites where project_id = %s",
                (run["project_id"],),
            ).fetchall()
        }
        bad_suites = sorted(
            {str(e.get("suite_id")) for e in entries} - known_suites
        )
        if bad_suites:
            return {
                "error": "suite(s) not in this project: " + ", ".join(bad_suites[:5])
            }
        clean = [
            {
                "test_case_id": str(e["test_case_id"]),
                "suite_id": str(e["suite_id"]),
                "spec_ref": str(e.get("spec_ref") or "").strip(),
            }
            for e in entries
        ]
        conn.execute(
            "update public.runs set spec_map = %s where id = %s",
            (json.dumps(clean), run_id),
        )
        conn.commit()
        return {"stored": len(clean)}


def store_test_evidence(
    settings: Settings,
    run_id: str,
    worker: dict[str, Any],
    evidence: dict[str, Any],
) -> dict[str, Any] | None:
    """US-81.6: hold a code run's pre-submit test outcome — worker-reported,
    a review signal, never the release gate. Same scoping as store_spec_map;
    the output tail is bounded so a log dump cannot bloat the run row."""
    import uuid as _uuid

    try:
        _uuid.UUID(run_id)
    except ValueError:
        return None
    with _connect(settings) as conn:
        run = conn.execute(
            "select id, worker_id, status from public.runs "
            "where id = %s and org_id = %s",
            (run_id, worker["org_id"]),
        ).fetchone()
        if not run:
            return None
        if str(run["worker_id"] or "") != str(worker["id"]) or run[
            "status"
        ] not in ("running", "succeeded"):
            return {
                "error": (
                    "only the claim holder (or the submitter while the run "
                    "is in review) can report test evidence for this run"
                )
            }

        def _int(v: Any) -> int | None:
            try:
                return int(v)
            except (TypeError, ValueError):
                return None

        clean = {
            "command": str(evidence.get("command") or "")[:500],
            "exit_code": _int(evidence.get("exit_code")),
            "passed": _int(evidence.get("passed")),
            "failed": _int(evidence.get("failed")),
            "skipped": _int(evidence.get("skipped")),
            "output_tail": str(evidence.get("output_tail") or "")[-4000:],
        }
        conn.execute(
            "update public.runs set test_evidence = %s where id = %s",
            (json.dumps(clean), run_id),
        )
        conn.commit()
        return {"ok": True, "evidence": clean}


def apply_spec_map(settings: Settings, run_id: str) -> int:
    """US-81.5: the merge happened — flip the mapped cases to automated.
    Idempotent; scoped to library cases (release copies keep what they had
    when their release was cut)."""
    with _connect(settings) as conn:
        run = conn.execute(
            "select spec_map, org_id from public.runs where id = %s", (run_id,)
        ).fetchone()
        entries = (run or {}).get("spec_map") or []
        n = 0
        for e in entries:
            if not (e.get("test_case_id") and e.get("suite_id") and e.get("spec_ref")):
                continue
            conn.execute(
                """
                update public.test_cases
                set execution = 'automated', suite_id = %s, spec_ref = %s
                where id = %s and org_id = %s and release_id is null
                """,
                (e["suite_id"], e["spec_ref"], e["test_case_id"], run["org_id"]),
            )
            n += 1
        conn.commit()
        return n


def list_project_suites(settings: Settings, project_id: str) -> list[dict[str, Any]]:
    """US-81.5: the project's active suites, compact — what a plan run needs
    to mark a case automated with a real target, and a code run needs to
    write specs where the pipeline will find them."""
    if not _valid_uuid(project_id):
        return []
    with _connect(settings) as conn:
        rows = conn.execute(
            """
            select id, name, layer, results_path from public.test_suites
            where project_id = %s and status = 'active'
            order by name
            """,
            (project_id,),
        ).fetchall()
    return [
        {
            "suite_id": str(r["id"]),
            "name": r["name"],
            "layer": r["layer"],
            "results_path": r["results_path"],
        }
        for r in rows
    ]


def materialize_test_cases(
    settings: Settings,
    org_id: str,
    project_id: str,
    issue_id: str,
    cases: list[dict[str, Any]],
) -> int:
    """US-17.4: service-role mirror of workflow._materialize_test_plan — replace
    the issue's agent test cases with a freshly-parsed set (a re-approve must
    not double the library)."""
    with _connect(settings) as conn:
        conn.execute(
            "update public.test_cases set status = 'abandoned' "
            "where issue_id = %s and source = 'agent' and status = 'active'",
            (issue_id,),
        )
        n = 0
        for tc in cases:
            conn.execute(
                """
                insert into public.test_cases
                  (org_id, project_id, issue_id, title, steps, expected_result,
                   source, test_types, environments, execution)
                values (%s, %s, %s, %s, %s, %s, 'agent', %s, %s, %s)
                """,
                (
                    org_id,
                    project_id,
                    issue_id,
                    tc.get("title") or "Untitled test",
                    tc.get("steps") or "",
                    tc.get("expected_result") or "",
                    json.dumps(tc.get("test_types") or []),
                    json.dumps(tc.get("environments") or ["dev"]),
                    tc.get("execution") or "manual",
                ),
            )
            n += 1
        conn.commit()
    return n


def get_project_repo(
    settings: Settings, org_shortname: str, project_slug: str, org_id: str
) -> dict[str, Any] | None:
    """Slug-addressed project → repo lookup (US-3.13). The shortname must
    name the authenticated worker's own org; cross-org and unknown names
    answer None (404), never an existence leak."""
    with _connect(settings) as conn:
        row = conn.execute(
            """
            select p.id, p.repo_full_name
            from public.projects p
            join public.organizations o on o.id = p.org_id
            where o.shortname = %s and p.slug = %s and p.org_id = %s
            """,
            (org_shortname, project_slug, org_id),
        ).fetchone()
    return row


def get_project_repo_by_id(
    settings: Settings, project_id: str, org_id: str
) -> dict[str, Any] | None:
    """Id-addressed project → repo/default-branch/slug/org-shortname lookup
    (no-claim MCP checkout, manager-triggered workspace prepare). Cross-org
    and unknown ids answer None, never an existence leak — same shape as
    get_project_repo, keyed the other way."""
    if not _valid_uuid(project_id):
        return None
    with _connect(settings) as conn:
        return conn.execute(
            """
            select p.id, p.name, p.repo_full_name, p.default_branch,
                   p.slug, o.shortname as org_shortname
            from public.projects p
            join public.organizations o on o.id = p.org_id
            where p.id = %s and p.org_id = %s
            """,
            (project_id, org_id),
        ).fetchone()


def get_project_guidelines_md(
    settings: Settings, project_id: str, org_id: str
) -> dict[str, Any] | None:
    """The project's assembled application guidelines — the same markdown
    the guidelines.md REST endpoint returns and AGENTS.md gets committed
    from (US-1.18, US-1.52). Cross-org and unknown ids answer None, never
    an existence leak."""
    if not _valid_uuid(project_id):
        return None
    with _connect(settings) as conn:
        return conn.execute(
            """
            select p.name,
                   public.assemble_project_guidelines(p.id) as guidelines
            from public.projects p
            where p.id = %s and p.org_id = %s
            """,
            (project_id, org_id),
        ).fetchone()


def get_project_learnings_md(
    settings: Settings, project_id: str, org_id: str
) -> dict[str, Any] | None:
    """The project's assembled learnings — the same markdown the
    learnings.md REST endpoint returns and dispatch snapshots into
    input_context (US-1.21), via the shared assemble_project_learnings
    function so the three never drift (US-5.3). Cross-org and unknown ids
    answer None, never an existence leak."""
    if not _valid_uuid(project_id):
        return None
    with _connect(settings) as conn:
        return conn.execute(
            """
            select p.name,
                   public.assemble_project_learnings(p.id) as learnings
            from public.projects p
            where p.id = %s and p.org_id = %s
            """,
            (project_id, org_id),
        ).fetchone()


def get_org_llm_config(
    settings: Settings, org_id: str
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """US-5.6: the org's LLM providers and function routes over the direct
    Postgres connection — worker-context calls (MCP) have no user JWT to
    go through PostgREST with. Same shapes _fetch_org_llm returns."""
    with _connect(settings) as conn:
        providers = conn.execute(
            "select id, org_id, name, provider_type, base_url, models, "
            "is_default, default_model, vault_secret_id "
            "from public.llm_providers where org_id = %s",
            (org_id,),
        ).fetchall()
        routes = conn.execute(
            "select function_key, provider_id, model "
            "from public.llm_function_routes where org_id = %s",
            (org_id,),
        ).fetchall()
    return list(providers), {r["function_key"]: dict(r) for r in routes}


def get_project_learnings_content(settings: Settings, project_id: str) -> str:
    """The raw learnings document row (not the assembled view) — the merge
    pipeline's input, mirroring the REST update path (US-1.21/US-5.6)."""
    if not _valid_uuid(project_id):
        return ""
    with _connect(settings) as conn:
        row = conn.execute(
            "select content from public.project_learnings where project_id = %s",
            (project_id,),
        ).fetchone()
    return row["content"] if row else ""


def upsert_project_learnings(
    settings: Settings,
    org_id: str,
    project_id: str,
    content: str,
    actor: dict[str, Any] | None = None,
) -> None:
    """Store the merged learnings document (last writer: the llm pipeline),
    mirroring the REST update path's upsert (US-1.21/US-5.6). US-5.33:
    `actor` ({type, id, name}) attributes the content_audit row the write
    triggers — without it a service-role write shows as 'system'."""
    with _connect(settings) as conn:
        if actor:
            conn.execute(
                """
                select set_config('app.audit_actor_type', %s, false),
                       set_config('app.audit_actor_id', %s, false),
                       set_config('app.audit_actor_name', %s, false)
                """,
                (
                    actor.get("type") or "worker",
                    str(actor.get("id") or ""),
                    actor.get("name") or "",
                ),
            )
        conn.execute(
            """
            insert into public.project_learnings
              (org_id, project_id, content, last_updated_by)
            values (%s, %s, %s, 'llm')
            on conflict (project_id) do update
              set content = excluded.content, last_updated_by = 'llm'
            """,
            (org_id, project_id, content),
        )
        conn.commit()


def record_learning_submission(
    settings: Settings,
    worker: dict[str, Any],
    org_id: str,
    project_id: str,
    text: str,
) -> str:
    """US-5.6/US-5.31: queue a worker's learning as a pending submission —
    the manager approves or rejects it on the Learnings tab; nothing
    reaches the curated document until approval."""
    with _connect(settings) as conn:
        row = conn.execute(
            """
            insert into public.learning_submissions
              (org_id, project_id, worker_id, text, status)
            values (%s, %s, %s, %s, 'pending')
            returning id
            """,
            (org_id, project_id, str(worker["id"]), text),
        ).fetchone()
        conn.commit()
    return str(row["id"])


def get_guideline_section(
    settings: Settings, project_id: str, section_key: str
) -> dict[str, Any] | None:
    """US-5.32: the project's guideline section by catalog key — the
    target a recommendation proposes to change."""
    with _connect(settings) as conn:
        row = conn.execute(
            """
            select id, section_key, title, content
            from public.project_guidelines
            where project_id = %s and section_key = %s
            limit 1
            """,
            (project_id, section_key),
        ).fetchone()
    return dict(row) if row else None


def list_guideline_section_keys(
    settings: Settings, project_id: str
) -> list[str]:
    with _connect(settings) as conn:
        rows = conn.execute(
            """
            select distinct section_key from public.project_guidelines
            where project_id = %s and section_key <> 'custom'
            order by section_key
            """,
            (project_id,),
        ).fetchall()
    return [r["section_key"] for r in rows]


def record_guideline_recommendation(
    settings: Settings,
    worker: dict[str, Any],
    org_id: str,
    project_id: str,
    section: dict[str, Any] | None,
    section_key: str,
    section_title: str,
    severity: str,
    proposed_text: str,
    rationale: str,
) -> dict[str, Any]:
    """US-5.32: queue a worker's guideline change proposal. Duplicate
    damping: an identical pending proposal from the same worker for the
    same section answers the existing row instead of piling up."""
    with _connect(settings) as conn:
        existing = conn.execute(
            """
            select id from public.guideline_recommendations
            where project_id = %s and worker_id = %s and status = 'pending'
              and section_key = %s and proposed_text = %s
            limit 1
            """,
            (project_id, str(worker["id"]), section_key, proposed_text),
        ).fetchone()
        if existing:
            return {"id": str(existing["id"]), "duplicate": True}
        row = conn.execute(
            """
            insert into public.guideline_recommendations
              (org_id, project_id, worker_id, section_id, section_key,
               section_title, severity, proposed_text, rationale)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            returning id
            """,
            (
                org_id,
                project_id,
                str(worker["id"]),
                section["id"] if section else None,
                section_key,
                section_title,
                severity,
                proposed_text,
                rationale,
            ),
        ).fetchone()
        conn.commit()
    return {"id": str(row["id"]), "duplicate": False}


# ---------------------------------------------------------------------------
# US-43: guidelines refresh
# ---------------------------------------------------------------------------

# What the agent is allowed to hand back in one pass. Twenty catalog sections
# plus a few proposed ones; past that it is not a guidelines pass any more.
MAX_REFRESH_SECTIONS = 30


# ---------------------------------------------------------------------------
# US-44.1: elaboration
# ---------------------------------------------------------------------------


def record_wireframe(
    settings: Settings,
    run: dict[str, Any],
    declaration: dict[str, Any],
) -> dict[str, Any]:
    """Store one `wireframe` artifact at `approved` and return its id.

    `approved`, not `draft`, and that is the whole difference from
    `record_elaboration` above: US-48.2 decided against a gate. A sketch is
    not a contract — the manager's lever is Redo with a comment, not an
    approvals row — so the artifact is live the moment it lands and the
    status column would otherwise be a permanent lie.

    The content is the DECLARATION the kit renders, never the rendered HTML.
    That is what lets US-48.5 rebuild every page from stored state, and what
    lets a kit upgrade restyle a repository's whole wireframe tree without
    re-running one agent."""
    with _connect(settings) as conn:
        version = _next_artifact_version(conn, str(run["issue_id"]), "wireframe")
        # Only one wireframe is ever current. A redo supersedes its
        # predecessor rather than sitting beside it, so "the wireframe" is
        # always a single row and the repo file always has one source.
        #
        # An ALLOW-list, deliberately — not "everything except superseded".
        # The rule US-27.10 wrote for run statuses holds for artifact statuses
        # too: a deny-list admits whatever value is added next, silently.
        # `tests/test_run_cancel.py` greps this file for the deny-list shape
        # (in its own text, so even a comment quoting one trips it) and that
        # is what caught this.
        conn.execute(
            """
            update public.artifacts set status = 'superseded'
            where issue_id = %s and kind = 'wireframe'
              and status in ('draft', 'approved')
            """,
            (run["issue_id"],),
        )
        row = conn.execute(
            # US-49.2: stamped like every other agent artifact. Only the PRD
            # displays it today; the data has to be there for the rest.
            """
            insert into public.artifacts
              (org_id, issue_id, kind, content, version, status, created_by,
               instruction_set)
            values (%s, %s, 'wireframe', %s, %s, 'approved', 'agent',
                    (select instruction_set from public.issues where id = %s))
            returning id
            """,
            (
                run["org_id"],
                run["issue_id"],
                json.dumps(declaration),
                version,
                run["issue_id"],
            ),
        ).fetchone()
        conn.commit()
    return {"id": str(row["id"]), "version": version}


def get_current_wireframe(
    settings: Settings, issue_id: str
) -> dict[str, Any] | None:
    """The live wireframe artifact for a work item, or None."""
    if not _valid_uuid(issue_id):
        return None
    with _connect(settings) as conn:
        row = conn.execute(
            """
            select id, issue_id, org_id, content, version, created_at
            from public.artifacts
            where issue_id = %s and kind = 'wireframe' and status = 'approved'
            order by version desc limit 1
            """,
            (issue_id,),
        ).fetchone()
    return dict(row) if row else None


def get_issue_for_wireframe(
    settings: Settings, issue_id: str
) -> dict[str, Any] | None:
    """What the repo writer needs to name a wireframe's file and title it."""
    if not _valid_uuid(issue_id):
        return None
    with _connect(settings) as conn:
        row = conn.execute(
            """
            select i.id, i.org_id, i.project_id, i.type, i.title,
                   i.item_no, i.sub_no, i.parent_id, e.number as epic_number
            from public.issues i
            left join public.epics e on e.id = i.epic_id
            where i.id = %s
            """,
            (issue_id,),
        ).fetchone()
    return dict(row) if row else None


def list_project_wireframes(
    settings: Settings, project_id: str, org_id: str
) -> list[dict[str, Any]]:
    """Every work item in the project that has been drawn or answered, in
    build order — what US-48.5's rebuild and index are generated from.

    Includes the `no UI surface` verdicts deliberately: the index has to be
    able to say "this was asked and the answer was no screen", which is a
    different thing from a story nobody has drawn."""
    if not _valid_uuid(project_id) or not _valid_uuid(org_id):
        return []
    with _connect(settings) as conn:
        rows = conn.execute(
            """
            select i.id, i.type, i.title, i.item_no, i.sub_no, i.parent_id,
                   i.abandoned_at, e.number as epic_number,
                   a.content, a.version, a.created_at,
                   p.item_no as parent_item_no, p.sub_no as parent_sub_no,
                   p.type as parent_type, p.title as parent_title,
                   pe.number as parent_epic_number
            from public.artifacts a
            join public.issues i on i.id = a.issue_id
            left join public.epics e on e.id = i.epic_id
            left join public.issues p on p.id = i.parent_id
            left join public.epics pe on pe.id = p.epic_id
            where a.kind = 'wireframe' and a.status = 'approved'
              and i.project_id = %s and i.org_id = %s
            order by e.number nulls last, i.item_no nulls last,
                     i.sub_no nulls last
            """,
            (project_id, org_id),
        ).fetchall()
    return [dict(row) for row in rows]


def record_elaboration(
    settings: Settings,
    run: dict[str, Any],
    story: str,
    acceptance_criteria: list[str],
    open_questions: list[str],
    proposes_change: bool,
) -> dict[str, Any]:
    """Store one `elaboration` artifact at `draft` and return its id.

    The proposal is an artifact rather than an in-place edit to
    `issues.body` for the reason the plan and PRD gates exist: written
    straight into the issue, the manager would have no before, no after, and
    no decision. `unique (issue_id, kind, version)` versions a
    re-elaboration for free."""
    payload = {
        "story": story,
        "acceptance_criteria": acceptance_criteria,
        "open_questions": open_questions,
        "proposes_change": proposes_change,
    }
    with _connect(settings) as conn:
        version = _next_artifact_version(conn, str(run["issue_id"]), "elaboration")
        # A superseded prior draft keeps the history readable: only one
        # elaboration is ever awaiting the manager on an item.
        conn.execute(
            """
            update public.artifacts set status = 'superseded'
            where issue_id = %s and kind = 'elaboration' and status = 'draft'
            """,
            (run["issue_id"],),
        )
        row = conn.execute(
            # US-49.2: stamped like every other agent artifact.
            """
            insert into public.artifacts
              (org_id, issue_id, kind, content, version, status, created_by,
               instruction_set)
            values (%s, %s, 'elaboration', %s, %s, 'draft', 'agent',
                    (select instruction_set from public.issues where id = %s))
            returning id
            """,
            (
                run["org_id"],
                run["issue_id"],
                json.dumps(payload),
                version,
                run["issue_id"],
            ),
        ).fetchone()
        conn.commit()
    return {"id": str(row["id"]), "version": version}


def get_open_guideline_refresh(
    settings: Settings, project_id: str
) -> dict[str, Any] | None:
    """US-43.2: the project's pending refresh, if it has one. A second
    dispatch is refused while this returns a row — a pass drafted against
    guidelines another pass is about to change is worth nothing, and the
    review surface has no way to show two proposals over one section."""
    if not _valid_uuid(project_id):
        return None
    with _connect(settings) as conn:
        row = conn.execute(
            """
            select r.id, r.issue_id, r.run_id, r.created_at, r.scope, r.focus,
                   (select count(*) from public.guideline_recommendations gr
                     where gr.refresh_id = r.id and gr.status = 'pending')
                     as pending_sections
            from public.guideline_refreshes r
            where r.project_id = %s and r.status = 'pending'
            limit 1
            """,
            (project_id,),
        ).fetchone()
    return dict(row) if row else None


def _work_item_digest(conn, project_id: str, limit: int = 120) -> list[dict[str, Any]]:
    """US-43.1: the project's delivery history, for a guidelines run's
    context. The MCP server deliberately exposes no backlog search ("no
    arbitrary story and document search"), and widening that surface for one
    run kind is a poor trade — so the factory assembles the digest instead.

    Completed items carry their body and acceptance criteria; everything else
    is a one-liner. That split is the whole point: what shipped is evidence,
    what is queued is intent."""
    rows = conn.execute(
        """
        select i.type, i.status, i.title, i.body,
               i.acceptance_criteria, i.updated_at,
               e.number as epic_no, i.item_no, i.sub_no
        from public.issues i
        left join public.epics e on e.id = i.epic_id
        where i.project_id = %s and i.abandoned_at is null
        order by (i.status in ('done', 'merged')) desc, i.updated_at desc
        limit %s
        """,
        (project_id, limit),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        prefix = {"bug": "BUG", "chore": "CHORE", "feature": "FEAT"}.get(
            r["type"], "US"
        )
        parts = [str(r["epic_no"] or 0), str(r["item_no"] or 0)]
        if r["sub_no"]:
            parts.append(str(r["sub_no"]))
        item: dict[str, Any] = {
            "id": f"{prefix}-{'.'.join(parts)}",
            "type": r["type"],
            "status": r["status"],
            "title": r["title"],
        }
        if r["status"] in ("done", "merged"):
            body = (r["body"] or "").strip()
            if body:
                item["body"] = body[:4000]
            if r["acceptance_criteria"]:
                item["acceptance_criteria"] = r["acceptance_criteria"]
        out.append(item)
    return out


def dispatch_guidelines_refresh(
    settings: Settings,
    org_id: str,
    project_id: str,
    scope: str,
    focus: str,
) -> dict[str, Any]:
    """US-43.2: create the chore and queue its run, atomically.

    Not dispatch_issue: that RPC infers plan-vs-code from an item's status and
    its approved plan — a ladder this kind does not climb, and there is no
    legal way to ask it for a `guidelines` run. Two steps would leave a chore
    nothing can dispatch, which is the whole reason this exists.

    Refusals are raised as ValueError for the router to translate; the
    project-budget refusal arrives from the BEFORE INSERT trigger on runs
    (US-37.2) and is deliberately not re-implemented here."""
    scope = scope if scope in ("all", "existing") else "all"
    focus = (focus or "").strip()

    with _connect(settings) as conn:
        project = conn.execute(
            """
            select id, name, repo_full_name, default_branch, org_id
            from public.projects where id = %s and org_id = %s
            """,
            (project_id, org_id),
        ).fetchone()
        if not project:
            raise ValueError("project not found")
        if not (project["repo_full_name"] or "").strip():
            raise ValueError(
                "this project has no linked repository — a guidelines refresh "
                "reads the source, so there is nothing for it to work from"
            )
        open_refresh = conn.execute(
            "select id from public.guideline_refreshes "
            "where project_id = %s and status = 'pending' limit 1",
            (project_id,),
        ).fetchone()
        if open_refresh:
            raise ValueError(
                f"refresh-in-flight:{open_refresh['id']}"
            )

        # No work item is created (US-43.6), so the scope and focus reach the
        # agent through the run's own context rather than through a chore's
        # body. The no-active-epic refusal went with the chore: a project that
        # cannot take a work item can still fix its guidelines.
        scope_instruction = (
            "Cover every section the repository supports, including ones "
            "this project has not filled in yet."
            if scope == "all"
            else "Refresh only sections that already exist — do not propose "
            "new ones, except Deployment and Release."
        )

        input_context = {
            "run_kind": "guidelines",
            "project_id": str(project_id),
            "project_name": project["name"],
            "repo_full_name": project["repo_full_name"],
            "default_branch": project["default_branch"],
            "scope": scope,
            "scope_instruction": scope_instruction,
            "focus": focus,
            "current_guidelines": _assembled_guidelines(conn, project_id),
            "work_items": _work_item_digest(conn, project_id),
        }

        # US-43.6: issue_id is NULL, and that is the whole fix. Modelled as a
        # chore, the run moved a work item to `in-review` at hand-back, which
        # put a second "needs your code review" row on Things to Do beside the
        # refresh card and sent the manager to a gate offering approve/reject
        # over a branch and a pull request that never existed. With no issue
        # there is no status to set and that gate is unreachable.
        #
        # project_id is passed explicitly: `runs_fill_project_id` only derives
        # it FROM an issue, and there is none.
        run = conn.execute(
            """
            insert into public.runs
              (org_id, project_id, issue_id, provider, status, kind,
               input_context, queue_rank)
            values (%s, %s, null, 'claude', 'queued', 'guidelines', %s, -1)
            returning id
            """,
            (org_id, project_id, json.dumps(input_context)),
        ).fetchone()

        refresh = conn.execute(
            """
            insert into public.guideline_refreshes
              (org_id, project_id, run_id, scope, focus)
            values (%s, %s, %s, %s, %s)
            returning id
            """,
            (org_id, project_id, run["id"], scope, focus),
        ).fetchone()
        conn.commit()

    return {
        "refresh_id": str(refresh["id"]),
        "run_id": str(run["id"]),
    }


def _assembled_guidelines(conn, project_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        select section_key, title, content
        from public.project_guidelines
        where project_id = %s
        order by sort_order asc
        """,
        (project_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def record_guidelines_refresh(
    settings: Settings,
    worker: dict[str, Any],
    run: dict[str, Any],
    summary: str,
    sections: list[dict[str, Any]],
) -> dict[str, Any]:
    """US-43.1: the whole pass, in one transaction.

    The bundle is created HERE rather than accumulated section by section as
    the agent works, so a run that fails, is cancelled, or dies mid-flight
    leaves nothing half-written for a manager to review."""
    org_id = str(run["org_id"])
    project_id = str(run["project_id"])
    with _connect(settings) as conn:
        refresh = conn.execute(
            """
            update public.guideline_refreshes
               set worker_id = %s, summary = %s,
                   status = case when %s then 'pending' else 'decided' end,
                   decided_at = case when %s then null else now() end
             where run_id = %s and status = 'pending'
            returning id
            """,
            (
                str(worker["id"]),
                summary,
                bool(sections),
                bool(sections),
                str(run["id"]),
            ),
        ).fetchone()
        if not refresh:
            # The dispatch always creates one, so this is a re-submit or a
            # refresh the manager already decided. Neither should silently
            # mint a second bundle.
            conn.rollback()
            return {"ok": False, "reason": "no open refresh for this run"}

        for s in sections:
            key = (s.get("section_key") or "").strip()
            section = None
            if key:
                section = conn.execute(
                    "select id, title from public.project_guidelines "
                    "where project_id = %s and section_key = %s limit 1",
                    (project_id, key),
                ).fetchone()
            title = (s.get("title") or "").strip() or (
                section["title"] if section else "Proposed section"
            )
            conn.execute(
                """
                insert into public.guideline_recommendations
                  (org_id, project_id, worker_id, section_id, section_key,
                   section_title, severity, proposed_text, rationale,
                   refresh_id)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    org_id,
                    project_id,
                    str(worker["id"]),
                    section["id"] if section else None,
                    key,
                    title,
                    s.get("severity") or "minor",
                    s.get("proposed_text") or "",
                    s.get("rationale") or "",
                    str(refresh["id"]),
                ),
            )

        # "I looked and have nothing to propose" is an answer. The refresh was
        # stamped `decided` above, and since US-43.6 there is no work item to
        # close alongside it.
        conn.commit()
    return {"ok": True, "refresh_id": str(refresh["id"]), "sections": len(sections)}


def decide_learning_submission(
    settings: Settings,
    submission_id: str,
    status: str,
    decided_by: str,
    note: str | None = None,
) -> bool:
    """US-5.31: stamp a pending submission approved/rejected. False when
    it wasn't pending (already decided, or unknown id) — the caller
    treats that as a lost race, never a silent double-decision."""
    with _connect(settings) as conn:
        row = conn.execute(
            """
            update public.learning_submissions
            set status = %s, decided_by = %s, decided_at = now(),
                decision_note = %s
            where id = %s and status = 'pending'
            returning id
            """,
            (status, decided_by, note or None, submission_id),
        ).fetchone()
        conn.commit()
    return bool(row)


def add_worker_comment(
    settings: Settings, run: dict[str, Any], worker: dict[str, Any], body: str
) -> dict[str, Any]:
    """A claim-holder's comment on its run's work item, plus the audit
    event (US-5.12). Caller enforces the claim guard."""
    with _connect(settings) as conn:
        row = conn.execute(
            """
            insert into public.issue_comments
              (org_id, issue_id, author_kind, author_worker, run_id, body)
            values (%s, %s, 'worker', %s, %s, %s)
            returning id, created_at
            """,
            (
                str(run["org_id"]),
                str(run["issue_id"]),
                str(worker["id"]),
                str(run["id"]),
                body,
            ),
        ).fetchone()
        conn.execute(
            """
            insert into public.issue_events (org_id, issue_id, type, payload)
            values (%s, %s, 'comment-added',
                    jsonb_build_object('comment_id', %s::text,
                                       'author_kind', 'worker'))
            """,
            (str(run["org_id"]), str(run["issue_id"]), str(row["id"])),
        )
        conn.commit()
    return row


def list_issue_comments_for_run(
    settings: Settings, run_id: str, org_id: str
) -> list[dict[str, Any]]:
    """The run's work item's comment thread, oldest first, with resolved
    author labels — so a claiming agent (including one picking up a retry)
    sees the whole prior discussion (US-5.12)."""
    if not _valid_uuid(run_id):
        return []
    with _connect(settings) as conn:
        rows = conn.execute(
            """
            select c.id, c.author_kind, c.body, c.created_at,
                   coalesce(w.name, p.display_name, p.email, 'member') as author
            from public.issue_comments c
            join public.runs r on r.issue_id = c.issue_id
            left join public.workers w on w.id = c.author_worker
            left join public.profiles p on p.id = c.author_user
            where r.id = %s::uuid and r.org_id = %s::uuid
              and c.org_id = r.org_id
            order by c.created_at
            """,
            (run_id, org_id),
        ).fetchall()
    return rows


def add_clarification(
    settings: Settings,
    run: dict[str, Any],
    worker: dict[str, Any],
    question: str,
    options: list[dict[str, str]] | None = None,
    multi_select: bool = False,
) -> dict[str, Any]:
    """US-5.4: a claim-holder's mid-run question to the manager, plus the
    audit event. Caller enforces the claim guard.

    US-14.9: `options` lets the agent offer the concrete resolutions it has
    already worked out, instead of describing them in prose and hoping the
    reply is unambiguous. Omitted, everything behaves exactly as before."""
    with _connect(settings) as conn:
        row = conn.execute(
            """
            insert into public.clarifications
              (org_id, issue_id, run_id, worker_id, question,
               options, multi_select)
            values (%s, %s, %s, %s, %s, %s, %s)
            returning id, asked_at
            """,
            (
                str(run["org_id"]),
                str(run["issue_id"]),
                str(run["id"]),
                str(worker["id"]),
                question,
                json.dumps(options) if options else None,
                multi_select,
            ),
        ).fetchone()
        conn.execute(
            """
            insert into public.issue_events (org_id, issue_id, type, payload)
            values (%s, %s, 'clarification-asked', %s)
            """,
            (
                str(run["org_id"]),
                str(run["issue_id"]),
                json.dumps(
                    {
                        "clarification_id": str(row["id"]),
                        "run_id": str(run["id"]),
                        "kind": run["kind"],
                        "worker": worker["name"],
                        "question": question,
                    }
                ),
            ),
        )
        conn.commit()
    return row


def list_run_clarifications(
    settings: Settings, run: dict[str, Any]
) -> list[dict[str, Any]]:
    """US-5.4: every clarification on the run's work item — this run's and
    prior runs' — oldest first, so answers survive re-dispatch and a retry
    claimer sees the whole exchange."""
    with _connect(settings) as conn:
        return conn.execute(
            """
            select c.id, c.run_id, c.question, c.answer,
                   -- US-14.9: the offered choices and what was picked, so
                   -- the worker reads a selection rather than parsing prose.
                   c.options, c.multi_select, c.selected_options,
                   c.asked_at, c.answered_at
            from public.clarifications c
            where c.issue_id = %s and c.org_id = %s
            order by c.asked_at
            """,
            (str(run["issue_id"]), str(run["org_id"])),
        ).fetchall()


def get_run_instructions(
    settings: Settings, run_id: str, org_id: str
) -> dict[str, Any] | None:
    """The work item's current instruction set for a queued or claimed run
    — NO claim required, so a worker can peek before claiming (US-5.11).
    Unknown, cross-org, and terminal runs all answer None (no existence
    leak)."""
    if not _valid_uuid(run_id):
        return None
    with _connect(settings) as conn:
        return conn.execute(
            """
            select r.id, r.kind, r.status,
                   i.id as issue_id, i.title as issue_title,
                   i.instruction_set
            from public.runs r
            join public.issues i on i.id = r.issue_id
            where r.id = %s and r.org_id = %s
              and r.status in ('queued', 'running')
            """,
            (run_id, org_id),
        ).fetchone()


def get_prompt_override(settings: Settings, prompt_key: str) -> str | None:
    """The superadmin's template override, or None for factory default —
    blank content counts as absent (US-5.17)."""
    with _connect(settings) as conn:
        row = conn.execute(
            "select nullif(trim(content), '') as content "
            "from public.llm_prompt_templates where prompt_key = %s",
            (prompt_key,),
        ).fetchone()
    return row["content"] if row else None


def list_prompt_overrides(settings: Settings) -> list[dict[str, Any]]:
    with _connect(settings) as conn:
        return conn.execute(
            "select prompt_key, content, updated_by, updated_at "
            "from public.llm_prompt_templates order by prompt_key"
        ).fetchall()


def upsert_prompt_override(
    settings: Settings, prompt_key: str, content: str, updated_by: str
) -> dict[str, Any]:
    with _connect(settings) as conn:
        row = conn.execute(
            """
            insert into public.llm_prompt_templates (prompt_key, content, updated_by)
            values (%s, %s, %s)
            on conflict (prompt_key) do update
              set content = excluded.content, updated_by = excluded.updated_by
            returning prompt_key, content, updated_by, updated_at
            """,
            (prompt_key, content, updated_by),
        ).fetchone()
        conn.commit()
    return row


def delete_prompt_override(settings: Settings, prompt_key: str) -> bool:
    with _connect(settings) as conn:
        cur = conn.execute(
            "delete from public.llm_prompt_templates where prompt_key = %s",
            (prompt_key,),
        )
        conn.commit()
        return cur.rowcount > 0


def get_baked_worker_instructions(settings: Settings) -> dict[str, str]:
    """All three baked worker-instruction defaults in one round trip."""
    with _connect(settings) as conn:
        rows = conn.execute(
            "select k as kind, public.baked_worker_instruction(k) as t "
            "from unnest(array['prd', 'plan', 'code']) as k"
        ).fetchall()
    return {r["kind"]: r["t"] for r in rows}


def get_baked_guideline_sections(
    settings: Settings, keys: list[str]
) -> dict[str, str]:
    """Baked guideline-section defaults for the given catalog keys, one
    round trip."""
    with _connect(settings) as conn:
        rows = conn.execute(
            "select k as key, public.baked_guideline_section(k) as t "
            "from unnest(%s::text[]) as k",
            (keys,),
        ).fetchall()
    return {r["key"]: r["t"] for r in rows if r["t"]}


def list_project_documents(
    settings: Settings, project_id: str, org_id: str, worker_id: str
) -> list[dict[str, Any]] | None:
    """US-5.8: the project's documents with ids to fetch, newest first.
    `linked_to_claim` marks documents attached to a work item this worker
    currently holds a claim on (or that item's governing PRD feature).
    Unknown and cross-org project ids answer None — no existence leak."""
    if not _valid_uuid(project_id):
        return None
    with _connect(settings) as conn:
        project = conn.execute(
            "select 1 from public.projects where id = %s and org_id = %s",
            (project_id, org_id),
        ).fetchone()
        if not project:
            return None
        return conn.execute(
            """
            select d.id, d.name, d.mime_type, d.attached_to, d.source,
                   d.updated_at, d.issue_id,
                   exists (
                     select 1
                     from public.runs r
                     join public.issues i on i.id = r.issue_id
                     where r.worker_id = %(worker)s and r.status = 'running'
                       and d.issue_id is not null
                       and (d.issue_id = i.id or d.issue_id = i.parent_id)
                   ) as linked_to_claim
            from public.documents d
            where d.project_id = %(project)s and d.org_id = %(org)s
            order by d.updated_at desc
            """,
            {"project": project_id, "org": org_id, "worker": worker_id},
        ).fetchall()


def list_run_documents(
    settings: Settings, run: dict[str, Any]
) -> list[dict[str, Any]]:
    """US-5.8: documents linked to a run's work item — its own attachments
    plus the governing PRD issue's documents (the parent feature for a
    story) — as id/title pointers for the work context.

    A project-scoped run has no work item and therefore no linked documents.
    Without this guard the null `issue_id` is stringified to the literal
    "None" and sent as a uuid, and `get_work_context` raises — which is what
    left a guidelines run with no instruction, no guidelines and no work-item
    digest, improvising from whatever it could find on disk."""
    if not _valid_uuid(str(run.get("issue_id") or "")):
        return []
    with _connect(settings) as conn:
        return conn.execute(
            """
            select d.id, d.name, d.attached_to, d.mime_type
            from public.documents d
            join public.issues i on i.id = %(issue)s
            where d.org_id = %(org)s
              and (d.issue_id = %(issue)s
                   or (i.parent_id is not null
                       and d.issue_id = i.parent_id
                       and d.attached_to = 'prd'))
            order by d.updated_at desc
            """,
            {"issue": str(run["issue_id"]), "org": str(run["org_id"])},
        ).fetchall()


def get_run_commands_section(
    settings: Settings, project_id: str
) -> str | None:
    """US-5.9: the project's 'Run commands' guideline section, read live at
    context-serve time — surfaced prominently in the work context so an
    agent can verify its own work before submitting. None when the section
    is absent or blank."""
    if not _valid_uuid(project_id):
        return None
    with _connect(settings) as conn:
        row = conn.execute(
            "select nullif(trim(content), '') as content "
            "from public.project_guidelines "
            "where project_id = %s and section_key = 'run-commands'",
            (project_id,),
        ).fetchone()
    return row["content"] if row else None


def get_project_environment(
    settings: Settings, project_id: str
) -> dict[str, Any] | None:
    """US-5.23: the project's declared environment as a structured object
    plus its rendered markdown (one SQL renderer shared with the AGENTS.md
    export). None when nothing is configured — empty stays absent."""
    if not _valid_uuid(project_id):
        return None
    with _connect(settings) as conn:
        row = conn.execute(
            "select env_runtime, env_setup_commands, env_notes, "
            "public.project_environment_md(id) as markdown "
            "from public.projects where id = %s",
            (project_id,),
        ).fetchone()
    if not row or not row["markdown"]:
        return None
    return {
        "runtime": (row["env_runtime"] or "").strip() or None,
        "setup_commands": [
            str(c).strip()
            for c in (row["env_setup_commands"] or [])
            if str(c).strip()
        ],
        "notes": (row["env_notes"] or "").strip() or None,
        "markdown": row["markdown"],
    }


def get_issue_scoring_context(
    settings: Settings, issue_id: str
) -> dict[str, Any] | None:
    """US-7.1: the fields needed to score an item's complexity — its type,
    title, spec, current basis, and (if present) the latest plan + test plan."""
    if not _valid_uuid(issue_id):
        return None
    with _connect(settings) as conn:
        issue = conn.execute(
            "select org_id, type, title, body, acceptance_criteria, "
            "complexity_basis from public.issues where id = %s",
            (issue_id,),
        ).fetchone()
        if not issue:
            return None
        plan = conn.execute(
            "select content from public.artifacts where issue_id = %s "
            "and kind = 'plan' and status in ('approved', 'draft') "
            "order by version desc limit 1",
            (issue_id,),
        ).fetchone()
        test = conn.execute(
            "select content from public.artifacts where issue_id = %s "
            "and kind = 'test_plan' and status in ('approved', 'draft') "
            "order by version desc limit 1",
            (issue_id,),
        ).fetchone()
    out = dict(issue)
    out["plan"] = plan["content"] if plan else None
    out["test_plan"] = test["content"] if test else None
    return out


def get_issue_assignee(settings: Settings, issue_id: str) -> dict[str, Any] | None:
    """US-9.9: the work item's intended assignee (principal), for informational
    display in get_work_context. Does not gate who may claim."""
    if not _valid_uuid(issue_id):
        return None
    with _connect(settings) as conn:
        row = conn.execute(
            "select p.kind, coalesce(p.display_name, p.email) as name "
            "from public.issues i "
            "join public.principals p on p.id = i.assignee_id "
            "where i.id = %s",
            (issue_id,),
        ).fetchone()
    if not row:
        return None
    return {"kind": row["kind"], "name": row["name"]}


def set_issue_complexity(
    settings: Settings,
    issue_id: str,
    *,
    complexity: str,
    touches_critical: bool,
    data_model_impact: str,
    rationale: str,
    basis: str,
    model: str | None,
) -> None:
    """US-7.1: write the advisory complexity estimate. A 'plan'-basis estimate
    is never downgraded by a later story-level call."""
    with _connect(settings) as conn:
        conn.execute(
            """
            update public.issues
            set complexity = %s, touches_critical = %s, data_model_impact = %s,
                complexity_rationale = %s, complexity_basis = %s,
                complexity_model = %s, complexity_scored_at = now()
            where id = %s
              and (complexity_basis is distinct from 'plan' or %s <> 'story')
            """,
            (
                complexity,
                touches_critical,
                data_model_impact,
                rationale,
                basis,
                model,
                issue_id,
                basis,
            ),
        )
        conn.commit()


def upsert_build_config_name(
    settings: Settings,
    org_id: str,
    project_id: str,
    name: str,
    actor: str | None = None,
) -> None:
    """US-7.9: record a build-config NAME (the value lives only in the data
    bucket, written by the api service role)."""
    with _connect(settings) as conn:
        conn.execute(
            """
            insert into public.project_build_config
                (org_id, project_id, name, updated_by)
            values (%s, %s, %s, %s)
            on conflict (project_id, name)
            do update set updated_at = now(), updated_by = excluded.updated_by
            """,
            (org_id, project_id, name, actor),
        )
        conn.commit()


def delete_build_config_name(
    settings: Settings, project_id: str, name: str
) -> None:
    with _connect(settings) as conn:
        conn.execute(
            "delete from public.project_build_config "
            "where project_id = %s and name = %s",
            (project_id, name),
        )
        conn.commit()


def list_build_config_names(settings: Settings, project_id: str) -> list[str]:
    if not _valid_uuid(project_id):
        return []
    with _connect(settings) as conn:
        rows = conn.execute(
            "select name from public.project_build_config "
            "where project_id = %s order by name",
            (project_id,),
        ).fetchall()
    return [r["name"] for r in rows]


def get_project_environment_websites(
    settings: Settings, project_id: str
) -> dict[str, str]:
    """US-7.2: the public Website of each classified (uat/production)
    deployment, keyed by environment — so a coding/QA agent testing an
    environment is told where to reach it. Empty when none are set."""
    if not _valid_uuid(project_id):
        return {}
    with _connect(settings) as conn:
        rows = conn.execute(
            "select environment, website_url from public.deployments "
            "where project_id = %s and website_url is not null "
            "and environment in ('uat', 'production') "
            "order by environment, updated_at desc",
            (project_id,),
        ).fetchall()
    websites: dict[str, str] = {}
    for row in rows:
        env = row["environment"]
        if env and env not in websites and (row["website_url"] or "").strip():
            websites[env] = row["website_url"].strip()
    return websites


def get_worker_instruction(
    settings: Settings,
    project_id: str,
    kind: str,
    issue_id: str | None = None,
) -> str | None:
    """The project's behavioral instruction text for a run kind — the
    manager-edited template, falling back to the factory default when blank
    (US-5.14). A live read at context-serve time, never frozen at dispatch.

    With issue_id, the kind first resolves through instruction_kind_for
    (us-96.1) so a type-differentiated item reads its own text — a chore's
    code run reads 'chore', not 'code'. Same SQL mapping the dispatch-time
    seed uses, so the two can never disagree."""
    if not _valid_uuid(project_id):
        return None
    with _connect(settings) as conn:
        if issue_id and _valid_uuid(issue_id):
            row = conn.execute(
                "select public.worker_instruction_for("
                "%s, public.instruction_kind_for(%s, %s)) as instruction",
                (project_id, issue_id, kind),
            ).fetchone()
        else:
            row = conn.execute(
                "select public.worker_instruction_for(%s, %s) as instruction",
                (project_id, kind),
            ).fetchone()
    return row["instruction"] if row else None


_ITEM_TYPE_PREFIX = {
    "feature": "FEAT",
    "story": "US",
    "bug": "BUG",
    "chore": "CHORE",
}


def work_item_display_id(
    item_type: str | None,
    epic_number: int | None,
    item_no: int | None,
    sub_no: int | None,
) -> str | None:
    """US-7.10 / US-7.15: the epic-scoped, type-prefixed work-item id
    (FEAT-1.4, US-1.4.1, BUG-1.5). None when the numbering is unavailable."""
    if epic_number is None or item_no is None:
        return None
    prefix = _ITEM_TYPE_PREFIX.get((item_type or "").strip(), "US")
    tail = f"{epic_number}.{item_no}.{sub_no}" if sub_no is not None else f"{epic_number}.{item_no}"
    return f"{prefix}-{tail}"


def _branch_slug(text: str | None, maxlen: int = 48) -> str:
    """US-7.3: a human-readable branch segment from a title — lowercased,
    non-alphanumerics collapsed to hyphens, truncated."""
    import re

    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    s = re.sub(r"-{2,}", "-", s)
    s = s[:maxlen].strip("-")
    return s or "work"


def resolve_working_branch(
    settings: Settings, run: dict[str, Any]
) -> tuple[str, str, str]:
    """US-7.3: the working branch for a coding run, from the project's dev
    branching strategy. Returns (branch, strategy, submit_mode). submit_mode
    is 'direct' for the main strategy (commit to default branch, no PR) or
    'pr' otherwise. A branch already stored on the run wins, so a later title
    edit never moves an existing branch. Names derive from the title under a
    factory/ prefix with a short stable id suffix; the main strategy uses the
    project's default branch as-is."""
    strategy = (run.get("dev_branch_strategy") or "story").strip() or "story"
    default_branch = (run.get("default_branch") or "main") or "main"
    if strategy == "main":
        submit_mode = "direct"
    else:
        submit_mode = "pr"

    stored = (run.get("branch_ref") or "").strip()

    # us-98.4: a merge ALWAYS lands behind a pull request, whatever the
    # project's dev strategy says. Conflict resolution is exactly the work
    # where an agent most easily drops somebody's change, and "the merge
    # succeeded" is not evidence it kept everything — a merged file that
    # compiles and reads cleanly can still have lost a whole function. The
    # `main` strategy's direct-commit mode would put that on the default
    # branch unreviewed, so it is overridden here rather than obeyed.
    if (run.get("kind") or "") == "merge":
        if stored:
            return stored, strategy, "pr"
        display = work_item_display_id(
            run.get("issue_type"),
            run.get("epic_number"),
            run.get("item_no"),
            run.get("sub_no"),
        )
        suffix = (
            display.lower() if display else str(run.get("issue_id") or "")[:6]
        )
        return f"factory/merge-{_branch_slug(suffix)}", strategy, "pr"

    if stored:
        return stored, strategy, submit_mode

    if strategy == "main":
        return default_branch, strategy, "direct"

    # Determine the item the branch is named for: the story itself, or its
    # parent feature when a feature's stories share one branch/PR.
    base_id = str(run.get("issue_id") or "")
    ic = run.get("input_context") or {}
    base_title = run.get("issue_title") or ic.get("title") or "work"
    parent_id = run.get("parent_id")
    if strategy == "work_item" and parent_id:
        if _valid_uuid(str(parent_id)):
            with _connect(settings) as conn:
                prow = conn.execute(
                    "select id, title from public.issues where id = %s",
                    (str(parent_id),),
                ).fetchone()
            if prow:
                base_id = str(prow["id"])
                base_title = prow["title"] or base_title

    branch = f"factory/{_branch_slug(base_title)}-{base_id[:6]}"
    return branch, strategy, submit_mode


def set_run_stopped_reason(settings: Settings, run_id: str, reason: str) -> None:
    """us-96.9: mark a running session the manager stopped, BEFORE the
    runner's hand-back arrives. The submit route already lands a failure
    report as outcome 'stopped' when the row carries a stopped_reason
    (US-33.2's mapping) — this gives that mapping its producer back, so a
    deliberate stop never reads as a malfunction. Best-effort by contract:
    callers must not fail the stop over this label."""
    with _connect(settings) as conn:
        conn.execute(
            "update public.runs set stopped_reason = %s "
            "where id = %s and status = 'running' and stopped_reason is null",
            (reason, run_id),
        )
        conn.commit()


def set_run_branch_ref(settings: Settings, run_id: str, branch: str) -> None:
    """Persist the resolved working branch on the run the first time it is
    computed (US-7.3), so it is stable across context fetches and matchable
    by the git proxy on hand-back. No-op when already set."""
    if not _valid_uuid(run_id):
        return
    with _connect(settings) as conn:
        conn.execute(
            "update public.runs set branch_ref = %s "
            "where id = %s and (branch_ref is null or branch_ref = '')",
            (branch, run_id),
        )
        conn.commit()


def get_running_run_for_branch_ref(
    settings: Settings, project_id: str, branch: str, worker_id: str
) -> dict[str, Any] | None:
    """US-7.3: the run backing a push to <branch>, matched by the branch_ref
    stored at context-serve time (replaces parsing the issue id out of the
    branch name, which no longer holds for title-slug branches).

    Project scoping is coalesce(i.project_id, r.project_id): ordinary
    plan/code runs are issue-linked and never carry their own project_id
    (dispatch_issue_kind_choice, migration 166, only sets org_id/issue_id),
    so the issues join is still how those resolve their project. Project-
    scoped, issue-less runs (release, deploy) have issue_id null instead — an
    inner join on i.id = r.issue_id can never match a null row, which
    permanently refused every release-run git push regardless of branch_ref.
    The left join + coalesce covers both without disturbing the common case."""
    if not (_valid_uuid(project_id) and _valid_uuid(worker_id)):
        return None
    with _connect(settings) as conn:
        row = conn.execute(
            """
            select r.id, r.pushed_head_sha
            from public.runs r
            left join public.issues i on i.id = r.issue_id
            where r.branch_ref = %s
              and coalesce(i.project_id, r.project_id) = %s
              and r.worker_id = %s and r.status = 'running'
            order by r.created_at desc
            limit 1
            """,
            (branch, project_id, worker_id),
        ).fetchone()
    return row


def get_claimed_run_for_branch(
    settings: Settings, project_id: str, issue_id: str, worker_id: str
) -> dict[str, Any] | None:
    """The run backing a factory/issue-<id> push: claimed by this worker,
    currently running, on this project."""
    if not (_valid_uuid(issue_id) and _valid_uuid(project_id)):
        return None
    with _connect(settings) as conn:
        row = conn.execute(
            """
            select r.id, r.pushed_head_sha
            from public.runs r
            join public.issues i on i.id = r.issue_id
            where r.issue_id = %s and i.project_id = %s
              and r.worker_id = %s and r.status = 'running'
            order by r.created_at desc
            limit 1
            """,
            (issue_id, project_id, worker_id),
        ).fetchone()
    return row


def record_branch_push(
    settings: Settings, run_id: str, head_sha: str, worker_name: str
) -> None:
    """Log a successful factory-remote push on the claimed run — the raw
    material for push-detection hand-back (US-3.4)."""
    with _connect(settings) as conn:
        run = conn.execute(
            """
            update public.runs
            set pushed_head_sha = %s, pushed_at = now()
            where id = %s
            returning org_id, issue_id, kind
            """,
            (head_sha, run_id),
        ).fetchone()
        # issue_events.issue_id is NOT NULL - project-scoped, issue-less runs
        # (release, deploy) have no issue to log against, same as claim_run's
        # activity-feed guard. Recording pushed_head_sha/pushed_at above still
        # happens unconditionally; only the issue-feed entry is skipped.
        if run and run["issue_id"]:
            conn.execute(
                """
                insert into public.issue_events (org_id, issue_id, type, payload)
                values (%s, %s, 'branch-pushed', %s)
                """,
                (
                    run["org_id"],
                    run["issue_id"],
                    json.dumps(
                        {
                            "run_id": str(run_id),
                            "kind": run["kind"],
                            "head_sha": head_sha,
                            "worker": worker_name,
                        }
                    ),
                ),
            )
        conn.commit()


def get_git_power_grant(
    settings: Settings, project_id: str, principal_id: str
) -> dict[str, Any] | None:
    """US-9.19: the Power Git grant (if any) for this principal on this project,
    joined with the project's default_branch so the proxy can enforce the
    default-branch rail. None = no grant, so the normal claim-based push policy
    applies. Runs server-side (service role); RLS is not the gate here — the
    grant's existence is."""
    if not (_valid_uuid(project_id) and _valid_uuid(principal_id)):
        return None
    with _connect(settings) as conn:
        return conn.execute(
            """
            select g.allow_default_branch, g.allow_force_push,
                   g.allow_branch_delete, g.allow_tag_push,
                   p.default_branch
            from public.git_power_grants g
            join public.projects p on p.id = g.project_id
            where g.project_id = %s and g.principal_id = %s
            """,
            (project_id, principal_id),
        ).fetchone()


def get_git_power_branch_head(
    settings: Settings, project_id: str, principal_id: str, branch: str
) -> str | None:
    """The head the factory last recorded for a power-pushed branch (US-9.19).
    Backs the allow_force_push rail: since the proxy has no object graph, a
    rewrite is 'old != this recorded head'. None on a branch's first push."""
    if not (_valid_uuid(project_id) and _valid_uuid(principal_id)):
        return None
    with _connect(settings) as conn:
        row = conn.execute(
            """
            select head_sha from public.git_power_branch_heads
            where project_id = %s and principal_id = %s and branch = %s
            """,
            (project_id, principal_id, branch),
        ).fetchone()
    return row["head_sha"] if row else None


def record_git_power_branch_head(
    settings: Settings, project_id: str, principal_id: str, branch: str, head_sha: str
) -> None:
    """Record a successful power push's head (US-9.19) so the next push to the
    same branch can be rewrite-checked. Upsert keyed on (project, principal,
    branch)."""
    with _connect(settings) as conn:
        conn.execute(
            """
            insert into public.git_power_branch_heads
              (project_id, principal_id, branch, head_sha, updated_at)
            values (%s, %s, %s, %s, now())
            on conflict (project_id, principal_id, branch)
            do update set head_sha = excluded.head_sha, updated_at = now()
            """,
            (project_id, principal_id, branch, head_sha),
        )
        conn.commit()


def get_run_for_documents(settings: Settings, run_id: str) -> dict[str, Any] | None:
    """Run + issue context for the document endpoints (US-2.21/2.22):
    org/project/issue, run status, and the governing PRD issue (the
    parent feature for a story, the issue itself for a feature)."""
    import uuid as _uuid

    try:
        _uuid.UUID(run_id)
    except ValueError:
        return None
    with _connect(settings) as conn:
        row = conn.execute(
            """
            select r.id, r.org_id, r.issue_id, r.status, r.worker_id, i.project_id,
                   coalesce(i.parent_id,
                            case when i.type = 'feature' then i.id end) as prd_issue_id
            from public.runs r
            join public.issues i on i.id = r.issue_id
            where r.id = %s
            """,
            (run_id,),
        ).fetchone()
    return dict(row) if row else None


def get_issue_sync_context(settings: Settings, issue_id: str) -> dict[str, Any] | None:
    """US-7.6: GitHub Issue sync is retired — always None. Kept as an inert
    stub so the (now no-op) push-back call path stays importable."""
    return None


def record_issue_event(
    settings: Settings,
    org_id: str,
    issue_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    with _connect(settings) as conn:
        conn.execute(
            """
            insert into public.issue_events (org_id, issue_id, type, payload)
            values (%s, %s, %s, %s)
            """,
            (org_id, issue_id, event_type, json.dumps(payload)),
        )
        conn.commit()


RESULT_BY_STATUS = {"passed": "pass", "failed": "fail", "blocked": "blocked"}


def count_unreported_test_cases(
    settings: Settings, run_id: str, worker_id: str
) -> int:
    """US-5.30: how many of the run's issue's test cases this worker has
    no reported result for yet — the count the code-submit response
    names so report_test_results doesn't get skipped."""
    if not _valid_uuid(run_id):
        return 0
    with _connect(settings) as conn:
        row = conn.execute(
            """
            select count(*) as unreported
            from public.test_cases tc
            join public.runs r on r.issue_id = tc.issue_id
            where r.id = %s
              and not exists (
                select 1
                from public.test_run_results trr
                join public.test_runs tr on tr.id = trr.test_run_id
                where tr.run_id = r.id
                  and tr.worker_id = %s
                  and tr.source = 'agent'
                  and trr.test_case_id = tc.id
              )
            """,
            (run_id, worker_id),
        ).fetchone()
    return int(row["unreported"]) if row else 0


def report_test_results(
    settings: Settings,
    run_id: str,
    worker: dict[str, Any],
    results: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """US-5.19: record a worker's pass/fail/blocked outcomes against the
    issue's test-case library as one agent-sourced test run per
    (factory run, worker), results upserted so re-reports replace that
    worker's previous outcome for a case. Returns None when the run isn't
    visible to the worker's org (no existence leak)."""
    import uuid as _uuid

    try:
        _uuid.UUID(run_id)
    except ValueError:
        return None
    with _connect(settings) as conn:
        run = conn.execute(
            """
            select r.id, r.org_id, r.issue_id, r.worker_id, r.status,
                   i.project_id
            from public.runs r
            join public.issues i on i.id = r.issue_id
            where r.id = %s and r.org_id = %s
            """,
            (run_id, worker["org_id"]),
        ).fetchone()
        if not run:
            return None
        # Claim holder while running, or the submitter while the run sits
        # unsettled in review — nobody else writes results for this run.
        if str(run["worker_id"] or "") != str(worker["id"]) or run[
            "status"
        ] not in ("running", "succeeded"):
            return {
                "error": (
                    "only the claim holder (or the submitter while the run "
                    "is in review) can report test results for this run"
                )
            }
        # US-22.9: a multi-story run may report against any case belonging to
        # a story it covers — and none outside it. run_issue_ids collapses to
        # runs.issue_id for a single-story run, so the scope is unchanged
        # there.
        known = {
            str(r["id"])
            for r in conn.execute(
                "select id from public.test_cases where issue_id in "
                "(select issue_id from public.run_issue_ids(%s))",
                (run["id"],),
            ).fetchall()
        }
        unknown = sorted(
            {
                str(r.get("test_case_id"))
                for r in results
                if str(r.get("test_case_id")) not in known
            }
        )
        if unknown:
            return {
                "error": (
                    "unknown test_case_id(s) for this work item: "
                    + ", ".join(unknown)
                ),
                "unknown_ids": unknown,
            }
        existing = conn.execute(
            """
            select id from public.test_runs
            where run_id = %s and worker_id = %s and source = 'agent'
            """,
            (run_id, worker["id"]),
        ).fetchone()
        if existing:
            test_run_id = existing["id"]
            conn.execute(
                "update public.test_runs set completed_at = now() where id = %s",
                (test_run_id,),
            )
        else:
            test_run_id = conn.execute(
                """
                insert into public.test_runs
                  (org_id, project_id, environment, label, status, source,
                   worker_id, run_id, worker_name, completed_at)
                values (%s, %s, 'agent', %s, 'completed', 'agent',
                        %s, %s, %s, now())
                returning id
                """,
                (
                    run["org_id"],
                    run["project_id"],
                    f"agent-verified · {worker['name']}",
                    worker["id"],
                    run_id,
                    worker["name"],
                ),
            ).fetchone()["id"]
        for r in results:
            conn.execute(
                """
                insert into public.test_run_results
                  (org_id, test_run_id, test_case_id, result, note, recorded_at)
                values (%s, %s, %s, %s, %s, now())
                on conflict (test_run_id, test_case_id) do update
                set result = excluded.result, note = excluded.note,
                    recorded_at = now()
                """,
                (
                    run["org_id"],
                    test_run_id,
                    str(r["test_case_id"]),
                    RESULT_BY_STATUS[r["status"]],
                    (r.get("evidence") or "").strip() or None,
                ),
            )
        conn.commit()
        return {
            "ok": True,
            "test_run_id": str(test_run_id),
            "recorded": len(results),
        }

# ---------------------------------------------------------------------------
# US-78.10: sessions with no work item
# ---------------------------------------------------------------------------


def open_agent_session(
    settings: Settings,
    org_id: str,
    project_id: str,
    worker_id: str,
    created_by_auth_user: str | None = None,
) -> dict[str, Any] | None:
    """Reserve a session row for this agent, or None when it already holds one.

    The refusal is the unique partial index doing its job (one live session per
    worker), caught here rather than raced in Python: two managers pressing the
    button at the same moment must not both get a session on one workspace.

    Also returns the project's git remote and any earlier ACP session id for
    this project+worker, so the runner can resume the conversation rather than
    start over (US-78.9's rule, applied to sessions).
    """
    if not (_valid_uuid(org_id) and _valid_uuid(project_id) and _valid_uuid(worker_id)):
        return None
    with _connect(settings) as conn:
        principal = None
        if created_by_auth_user:
            row = conn.execute(
                "select id from public.principals where auth_user_id = %s",
                (created_by_auth_user,),
            ).fetchone()
            principal = row["id"] if row else None
        try:
            row = conn.execute(
                """
                insert into public.agent_sessions
                    (org_id, project_id, worker_id, created_by, status)
                values (%s, %s, %s, %s, 'opening')
                returning id
                """,
                (org_id, project_id, worker_id, principal),
            ).fetchone()
        except Exception:  # noqa: BLE001 — the unique index, almost always
            conn.rollback()
            return None
        prior = conn.execute(
            """
            select acp_session_id from public.agent_sessions
            where worker_id = %s and project_id = %s and acp_session_id is not null
            order by created_at desc limit 1
            """,
            (worker_id, project_id),
        ).fetchone()
        remote = conn.execute(
            """
            select o.shortname, p.slug
            from public.projects p join public.organizations o on o.id = p.org_id
            where p.id = %s
            """,
            (project_id,),
        ).fetchone()
        conn.commit()
    # The factory's own git remote, the same address a run is handed
    # (`factory_mcp` builds it per-request; here there is no request, so it
    # comes from the configured API base).
    base = (getattr(settings, "api_base_url", "") or "").rstrip("/")
    git_remote_url = (
        f"{base}/git/{remote['shortname']}/{remote['slug']}.git" if remote else None
    )
    return {
        "id": str(row["id"]),
        "git_remote_url": git_remote_url,
        "acp_session_id": (prior or {}).get("acp_session_id"),
    }


def mark_agent_session_open(
    settings: Settings,
    session_id: str,
    acp_session_id: str | None = None,
    workspace_path: str | None = None,
) -> None:
    if not _valid_uuid(session_id):
        return
    with _connect(settings) as conn:
        conn.execute(
            """
            update public.agent_sessions
            set status = 'open', acp_session_id = coalesce(%s, acp_session_id),
                workspace_path = coalesce(%s, workspace_path), last_active_at = now()
            where id = %s
            """,
            (acp_session_id, workspace_path, session_id),
        )
        conn.commit()


def finish_agent_session(
    settings: Settings,
    session_id: str,
    status: str,
    error: str | None = None,
    worker_id: str | None = None,
) -> None:
    """Close (or fail) a session row. Idempotent: closing a closed session is
    what a restart looks like, not an error.

    US-83.3: `worker_id`, when given, scopes the update to a session that
    worker actually holds — the `session.failed` control message is believed
    only about the caller's own sessions, never anyone else's.

    The check is written as an allow-list on purpose:
    `test_no_query_selects_runs_by_excluding_terminal_statuses` scans this file
    for deny-list status predicates. It is guarding SQL that selects live runs,
    and this is argument validation — but a regex cannot tell those apart, and
    the guard is worth more kept strict than bent around one caller."""
    is_terminal = status in ("closed", "failed")
    if not (_valid_uuid(session_id) and is_terminal):
        return
    if worker_id is not None and not _valid_uuid(worker_id):
        return
    with _connect(settings) as conn:
        conn.execute(
            """
            update public.agent_sessions
            set status = %s, error = coalesce(%s, error), closed_at = now()
            where id = %s and status in ('opening', 'open')
              and (%s::uuid is null or worker_id = %s)
            """,
            (status, error, session_id, worker_id, worker_id),
        )
        conn.commit()


def record_agent_session_event(
    settings: Settings, session_id: str, kind: str, content: str
) -> bool:
    """One transcript line. Bumps `last_active_at`, which is what the idle
    timeout measures — so "idle" means the AGENT went quiet, not that nobody
    was watching."""
    if not _valid_uuid(session_id):
        return False
    if kind not in RUN_TRACE_KINDS:
        kind = DEFAULT_RUN_TRACE_KIND
    with _connect(settings) as conn:
        row = conn.execute(
            "select org_id from public.agent_sessions where id = %s", (session_id,)
        ).fetchone()
        if not row:
            return False
        conn.execute(
            """
            insert into public.agent_session_events (session_id, org_id, kind, content)
            values (%s, %s, %s, %s)
            """,
            (session_id, row["org_id"], kind, content),
        )
        conn.execute(
            "update public.agent_sessions set last_active_at = now() where id = %s",
            (session_id,),
        )
        conn.commit()
    return True


def session_model(settings: Settings, worker_id: str) -> str:
    """The model a CLI-window session reasons with — the agent's own `code`
    role, the closest thing a free-form conversation has to a kind. Empty when
    the agent has none, which the CALLER must turn into a refusal (US-78.5's
    rule: no model, no session — never a CLI falling back to a default nobody
    chose).

    US-83.2: this replaces `session_model_env`, a stub that returned
    `{"model": ...}` while the runner expected the full gateway env — no key
    was ever minted, so a session's CLI would have started credential-less and
    the no-model refusal (keyed on GROK_MODELS_BASE_URL) could never fire.
    The env is now built where runs build theirs: a real mint plus
    `llm_gateway.module_env`, in the session-open path."""
    if not _valid_uuid(worker_id):
        return ""
    with _connect(settings) as conn:
        row = conn.execute(
            "select model_overrides, model_routes from public.runner_config where worker_id = %s",
            (worker_id,),
        ).fetchone()
    overrides = (row or {}).get("model_overrides") or {}
    routes = (row or {}).get("model_routes") or {}
    return str(overrides.get("code") or routes.get("code") or "")


def idle_agent_sessions(settings: Settings, minutes: int) -> list[dict[str, Any]]:
    """Sessions whose agent has been silent past the timeout — the sweep's input."""
    with _connect(settings) as conn:
        rows = conn.execute(
            """
            select id, worker_id from public.agent_sessions
            where status in ('opening', 'open')
              and last_active_at < now() - make_interval(mins => %s)
            """,
            (minutes,),
        ).fetchall()
    return [dict(r) for r in rows]
