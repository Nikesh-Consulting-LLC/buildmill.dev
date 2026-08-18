"""US-119.1: a dead UAT deploy settles the release.

On 2026-08-18 release 2026.08.18.2's UAT deploy fired at 12:26:27 UTC and the
API process restarted eight seconds later. The startup reaper failed the
`deployment_runs` row — and the release stayed `deploying` for nine and a
half hours, because `_settle_release_deploy` lived only inside
`run_pipeline`'s terminal branches, which a restart skips. From `deploying`
there was no legal move: Stop refused it, Retry took only failed states, the
page had no button, and migrations 215/275 froze the project.

These pin the rule the story installs: every writer that ends a
release-linked deploy run settles the release; a `deploying` release is
re-read from its run on a sweep; and Stop reaches `deploying`. All against
a fake connection — Essential, no database.
"""

from __future__ import annotations

import uuid

import pytest

from app import deploy
from app.config import Settings

RELEASE_ID = str(uuid.uuid4())
RUN_ID = str(uuid.uuid4())
ORG_ID = str(uuid.uuid4())


@pytest.fixture()
def settings():
    return Settings(
        supabase_url="https://test.supabase.co",
        supabase_publishable_key="sb_publishable_test",
        cors_origins="http://localhost:3000",
        database_url="postgresql://test",
    )


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None


class FakeConn:
    """A scripted connection: each execute() pops the next result and records
    the statement (whitespace-collapsed) with its params."""

    def __init__(self, script=None):
        self.script = list(script or [])
        self.calls: list[tuple[str, tuple | None]] = []
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.calls.append((" ".join(str(sql).split()), params))
        rows = self.script.pop(0) if self.script else []
        return _Cursor(rows)

    def commit(self):
        self.commits += 1


def _statements(conn: FakeConn) -> list[str]:
    return [sql for sql, _ in conn.calls]


# --- the reaper (AC1) ---------------------------------------------------------


def test_reaper_settles_the_release_of_every_release_linked_run(settings, monkeypatch):
    conn = FakeConn(
        script=[
            [
                {"id": RUN_ID, "org_id": ORG_ID, "release_id": RELEASE_ID},
                {"id": "run-plain", "org_id": ORG_ID, "release_id": None},
            ]
        ]
    )
    monkeypatch.setattr(deploy, "_connect", lambda s: conn)
    settled: list[tuple] = []
    monkeypatch.setattr(
        deploy,
        "_settle_release_deploy",
        lambda s, run_id, outcome, reason=None: settled.append((run_id, outcome, reason)),
    )

    assert deploy.reap_orphaned_runs(settings) == 2

    # Both rows reaped exactly as US-1.32 always did: one UPDATE, one event each.
    stmts = _statements(conn)
    assert stmts[0].startswith("update public.deployment_runs set status = 'failed'")
    assert "returning id, org_id, release_id" in stmts[0]
    assert sum(1 for s in stmts if "insert into public.deployment_run_events" in s) == 2
    # The note reaches the run log through a parameter, not string-building.
    assert conn.calls[0][1] == (f"\n[{deploy.REAPED_NOTE}]",)
    # Only the release-linked run settles its release, and it says why.
    assert settled == [
        (RUN_ID, "failed", "the UAT deploy was interrupted by API server restart")
    ]


# --- request_cancel with no live pipeline (AC2) -----------------------------


def test_cancel_without_a_live_pipeline_settles_the_release(settings, monkeypatch):
    conn = FakeConn(script=[[{"org_id": ORG_ID}], []])
    monkeypatch.setattr(deploy, "_connect", lambda s: conn)
    monkeypatch.setattr(deploy, "record_event", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "_RUNNING", {})
    settled: list[tuple] = []
    monkeypatch.setattr(
        deploy,
        "_settle_release_deploy",
        lambda s, run_id, outcome, reason=None: settled.append((run_id, outcome, reason)),
    )

    outcome = deploy.request_cancel(settings, RUN_ID, "user-1", "kaushlesh@nikesh.llc")

    assert outcome == "marked"
    assert settled == [
        (
            RUN_ID,
            "cancelled",
            "the UAT deploy was cancelled by kaushlesh@nikesh.llc; no pipeline was running",
        )
    ]


def test_cancel_of_a_live_pipeline_leaves_the_settle_to_the_pipeline(
    settings, monkeypatch
):
    """The signalled branch is unchanged: the task's own CancelledError
    handler settles, so this must NOT settle a second time."""

    class _Task:
        cancelled = False

        def done(self):
            return False

        def cancel(self):
            self.cancelled = True

    task = _Task()
    conn = FakeConn(script=[[{"org_id": ORG_ID}]])
    monkeypatch.setattr(deploy, "_connect", lambda s: conn)
    monkeypatch.setattr(deploy, "record_event", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "_RUNNING", {RUN_ID: task})
    settled: list[tuple] = []
    monkeypatch.setattr(
        deploy,
        "_settle_release_deploy",
        lambda s, run_id, outcome, reason=None: settled.append((run_id, outcome, reason)),
    )

    assert deploy.request_cancel(settings, RUN_ID, "user-1", "x@y") == "signalled"
    assert task.cancelled
    assert settled == []


def test_cancel_of_a_finished_run_is_not_active_and_settles_nothing(
    settings, monkeypatch
):
    conn = FakeConn(script=[[]])
    monkeypatch.setattr(deploy, "_connect", lambda s: conn)
    settled: list = []
    monkeypatch.setattr(
        deploy, "_settle_release_deploy", lambda *a, **k: settled.append(a)
    )
    assert deploy.request_cancel(settings, RUN_ID, "user-1", "x@y") == "not-active"
    assert settled == []


# --- the one UPDATE behind every settle -------------------------------------


def test_settle_status_is_guarded_to_deploying_and_carries_the_reason(
    settings, monkeypatch
):
    conn = FakeConn(script=[[{"id": RELEASE_ID}]])
    monkeypatch.setattr(deploy, "_connect", lambda s: conn)

    moved = deploy._settle_release_status(
        settings, RELEASE_ID, "uat-deploy-failed", "the UAT deploy failed: ssh timed out"
    )

    assert moved is True
    sql, params = conn.calls[0]
    assert "where id = %s and status = 'deploying'" in sql
    assert params[0] == "uat-deploy-failed"
    assert params[-1] == RELEASE_ID
    assert "the UAT deploy failed: ssh timed out" in params
    assert conn.commits == 1


def test_settle_status_reports_when_someone_else_moved_the_release_first(
    settings, monkeypatch
):
    conn = FakeConn(script=[[]])
    monkeypatch.setattr(deploy, "_connect", lambda s: conn)
    assert (
        deploy._settle_release_status(settings, RELEASE_ID, "uat-deploy-failed", "x")
        is False
    )


def test_settle_deploy_maps_outcome_to_status_and_keeps_success_reasonless(
    settings, monkeypatch
):
    conn = FakeConn(script=[[{"release_id": RELEASE_ID}], [{"release_id": RELEASE_ID}]])
    monkeypatch.setattr(deploy, "_connect", lambda s: conn)
    calls: list[tuple] = []
    monkeypatch.setattr(
        deploy,
        "_settle_release_status",
        lambda s, rid, status, reason, deployed_at=None: calls.append(
            (rid, status, reason)
        )
        or True,
    )
    deploy._settle_release_deploy(settings, RUN_ID, "succeeded", "ignored")
    deploy._settle_release_deploy(settings, RUN_ID, "failed", "the UAT deploy failed: x")
    assert calls == [
        (RELEASE_ID, "uat-deployed", None),
        (RELEASE_ID, "uat-deploy-failed", "the UAT deploy failed: x"),
    ]


def test_settle_deploy_is_a_no_op_for_an_ordinary_run(settings, monkeypatch):
    conn = FakeConn(script=[[{"release_id": None}]])
    monkeypatch.setattr(deploy, "_connect", lambda s: conn)
    calls: list = []
    monkeypatch.setattr(
        deploy, "_settle_release_status", lambda *a, **k: calls.append(a) or True
    )
    deploy._settle_release_deploy(settings, RUN_ID, "failed", "x")
    assert calls == []


# --- the sweep (AC3) ------------------------------------------------------------


def _stranded(**over):
    row = {
        "release_id": RELEASE_ID,
        "version": "2026.08.18.2",
        "uat_deployment_run_id": RUN_ID,
        "run_id": RUN_ID,
        "org_id": ORG_ID,
        "run_status": "failed",
        "run_created_at": "2026-08-18T12:26:27+00:00",
        "run_finished_at": "2026-08-18T12:26:35+00:00",
        "run_log": "\n[interrupted by API server restart]",
        "timeout_minutes": 30,
        "run_age_minutes": 570.0,
    }
    row.update(over)
    return row


def _run_sweep(settings, monkeypatch, rows, *, running=None):
    conn = FakeConn(script=[rows])
    monkeypatch.setattr(deploy, "_connect", lambda s: conn)
    monkeypatch.setattr(deploy, "_RUNNING", running or {})
    settled: list[dict] = []

    def fake_settle(s, rid, status, reason, deployed_at=None):
        settled.append(
            {"release_id": rid, "status": status, "reason": reason, "deployed_at": deployed_at}
        )
        return True

    monkeypatch.setattr(deploy, "_settle_release_status", fake_settle)
    result = deploy.settle_stranded_release_deploys(settings)
    return conn, settled, result


def test_sweep_moves_a_release_whose_run_failed_and_quotes_the_run(
    settings, monkeypatch
):
    """The 2026.08.18.2 shape exactly: run reaped, release still deploying."""
    conn, settled, result = _run_sweep(settings, monkeypatch, [_stranded()])
    assert settled == [
        {
            "release_id": RELEASE_ID,
            "status": "uat-deploy-failed",
            "reason": "the UAT deploy failed — [interrupted by API server restart]",
            "deployed_at": None,
        }
    ]
    assert result[0]["landed"] == "uat-deploy-failed"
    assert result[0]["from_run_status"] == "failed"
    assert result[0]["version"] == "2026.08.18.2"
    # Only the select ran — a terminal run is not touched again.
    assert len(conn.calls) == 1


def test_sweep_moves_a_release_whose_run_was_cancelled(settings, monkeypatch):
    _, settled, _ = _run_sweep(
        settings, monkeypatch, [_stranded(run_status="cancelled", run_log=None)]
    )
    assert settled[0]["status"] == "uat-deploy-failed"
    assert settled[0]["reason"] == "the UAT deploy cancelled"


def test_sweep_moves_a_release_whose_run_is_gone(settings, monkeypatch):
    _, settled, result = _run_sweep(
        settings,
        monkeypatch,
        [_stranded(run_id=None, run_status=None, org_id=None, uat_deployment_run_id=None)],
    )
    assert settled[0]["status"] == "uat-deploy-failed"
    assert settled[0]["reason"] == "the UAT deploy run no longer exists"
    assert result[0]["run_id"] is None


def test_sweep_lands_a_swallowed_success_at_uat_deployed(settings, monkeypatch):
    _, settled, result = _run_sweep(
        settings, monkeypatch, [_stranded(run_status="succeeded")]
    )
    assert settled == [
        {
            "release_id": RELEASE_ID,
            "status": "uat-deployed",
            "reason": None,
            "deployed_at": "2026-08-18T12:26:35+00:00",
        }
    ]
    assert result[0]["landed"] == "uat-deployed"


def test_sweep_fails_a_run_past_its_timeout_that_no_process_holds(
    settings, monkeypatch
):
    conn, settled, result = _run_sweep(
        settings,
        monkeypatch,
        [_stranded(run_status="running", run_age_minutes=36.0, timeout_minutes=30)],
    )
    # 36 > 30 + 5: the run is failed with a note and an event, then the
    # release settles off it.
    stmts = _statements(conn)
    assert any(
        s.startswith("update public.deployment_runs set status = 'failed'")
        and "where id = %s and status in ('queued', 'running')" in s
        for s in stmts
    )
    assert any("insert into public.deployment_run_events" in s for s in stmts)
    note = next(p for s, p in conn.calls if "set status = 'failed'" in s)[0]
    assert note.startswith("\n[timed out: no pipeline reported for 36 minutes (limit 30)")
    assert settled[0]["status"] == "uat-deploy-failed"
    assert settled[0]["reason"].startswith("the UAT deploy timed out: no pipeline reported")
    assert result[0]["from_run_status"] == "running"


def test_sweep_leaves_a_young_run_alone(settings, monkeypatch):
    conn, settled, result = _run_sweep(
        settings,
        monkeypatch,
        [_stranded(run_status="running", run_age_minutes=34.9, timeout_minutes=30)],
    )
    assert settled == [] and result == []
    assert len(conn.calls) == 1


def test_sweep_leaves_a_run_this_process_is_running_alone(settings, monkeypatch):
    class _Live:
        def done(self):
            return False

    conn, settled, result = _run_sweep(
        settings,
        monkeypatch,
        [_stranded(run_status="running", run_age_minutes=400.0)],
        running={RUN_ID: _Live()},
    )
    assert settled == [] and result == []
    assert len(conn.calls) == 1


def test_sweep_honours_a_deployments_own_longer_timeout(settings, monkeypatch):
    _, settled, _ = _run_sweep(
        settings,
        monkeypatch,
        [_stranded(run_status="running", run_age_minutes=60.0, timeout_minutes=90)],
    )
    assert settled == []


def test_sweep_with_nothing_deploying_touches_nothing(settings, monkeypatch):
    conn, settled, result = _run_sweep(settings, monkeypatch, [])
    assert result == [] and settled == []
    assert len(conn.calls) == 1


# --- the pipeline's own last word (AC7) --------------------------------------


def test_last_log_line_quotes_the_runs_final_line_or_nothing():
    assert deploy._last_log_line(None) == ""
    assert deploy._last_log_line("   \n  ") == ""
    assert (
        deploy._last_log_line("resolving head\n[failed: health check refused]\n")
        == " — [failed: health check refused]"
    )
