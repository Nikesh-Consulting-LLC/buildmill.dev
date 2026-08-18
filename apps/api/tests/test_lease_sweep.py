"""us-119.2: a poll costs one query — the lease sweeps run on their own clock.

Endpoint-level and loop-level, database-free: the point is *where* the
sweeps run (a 30 s task in `sweeps.py`) and *where they no longer run* (the
runner's poll path), not what they do — `test_worker_pool_sql.py` and
`test_release_prep_reaper_sql.py` cover the SQL.
"""

import asyncio
import uuid

import pytest

from app import db, reconcile, sweeps
from app.routers import worker as worker_router

ORG_ID = str(uuid.uuid4())
WORKER = {
    "id": str(uuid.uuid4()),
    "org_id": ORG_ID,
    "name": "Runner",
    "type": "autonomous",
    "status": "active",
}
HDR = {"X-Worker-Token": "sfw_testtoken"}


@pytest.fixture
def worker_auth(monkeypatch):
    monkeypatch.setattr(
        "app.routers.worker.db.get_worker_by_token",
        lambda settings, token: dict(WORKER) if token == "sfw_testtoken" else None,
    )
    return WORKER


class _Tripwire:
    """A stand-in that records whether it was called at all."""

    def __init__(self, name):
        self.name = name
        self.calls = 0

    def __call__(self, *a, **k):
        self.calls += 1
        return [] if "reap" in self.name or "list" in self.name else 0


# ---------------------------------------------------------------- AC1


def test_the_pool_listing_reconciles_nothing(client, worker_auth, monkeypatch):
    """GET /worker/pool lists and does nothing else — the reconciler and the
    expired-claim requeue that used to run first are the sweep's business."""
    reconcile_tw = _Tripwire("reconcile")

    async def _reconcile(settings):
        reconcile_tw.calls += 1
        return 0

    requeue_tw = _Tripwire("requeue")
    monkeypatch.setattr(reconcile, "reconcile_pushed_expired_claims", _reconcile)
    monkeypatch.setattr(db, "requeue_expired_claims", requeue_tw)
    monkeypatch.setattr("app.routers.worker.db.list_worker_pool", lambda s, w: [])
    monkeypatch.setattr("app.routers.worker.db.list_worker_resumable", lambda s, w: [])

    resp = client.get("/api/v1/worker/pool", headers=HDR)

    assert resp.status_code == 200
    assert resp.json() == {"runs": [], "resumable": []}
    assert reconcile_tw.calls == 0, "the reconciler ran on the poll path"
    assert requeue_tw.calls == 0, "the expired-claim requeue ran on the poll path"


def test_the_release_prep_listing_reaps_nothing(client, worker_auth, monkeypatch):
    """GET /worker/release-prep lists; `reap_expired_release_preps` is the
    sweep's. Faked at the db function the listing calls, so this pins the
    handler and the db wrapper both."""
    reap_tw = _Tripwire("reap")
    monkeypatch.setattr(db, "reap_expired_release_preps", reap_tw)
    monkeypatch.setattr(db, "_list_release_prep_pool", lambda s, org: [])

    resp = client.get("/api/v1/worker/release-prep", headers=HDR)

    assert resp.status_code == 200
    assert resp.json() == {"items": []}
    assert reap_tw.calls == 0, "the release-prep reaper ran on the poll path"


def test_the_pool_query_carries_the_paused_predicate(monkeypatch):
    """us-119.2: the paused check used to be a second connection before the
    listing; it is a predicate of the one listing query now."""
    calls = []

    class _Cur:
        def fetchall(self):
            return []

    class _Conn:
        def execute(self, q, p=None):
            calls.append((" ".join(q.split()), p))
            return _Cur()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(db, "_connect", lambda s: _Conn())
    db.list_worker_pool(object(), {"id": WORKER["id"], "org_id": ORG_ID, "type": "autonomous"})
    assert len(calls) == 1, "one query, not a paused read plus a listing"
    q, _ = calls[0]
    assert "rc.paused" in q and "runner_config" in q


# ---------------------------------------------------------------- AC2


def test_the_sweep_survives_a_raising_tick_and_a_hung_one():
    """Three ticks: the first raises, the second exceeds the time-box, the
    third runs. Every tick starts on schedule; nothing stops the loop but
    cancellation."""
    seen = []

    async def tick(settings):
        seen.append(len(seen) + 1)
        if len(seen) == 1:
            raise RuntimeError("database went away")
        if len(seen) == 2:
            await asyncio.sleep(10)  # far past the box
        return {"ok": True}

    ticks = asyncio.run(
        sweeps.run_lease_sweep(
            lambda: object(),
            interval_s=0,
            tick_timeout_s=0.05,
            tick=tick,
            max_ticks=3,
        )
    )
    assert ticks == 3
    assert seen == [1, 2, 3]


def test_the_sweep_ticks_first_then_waits():
    """A restart sweeps at once — the first tick does not wait an interval."""
    started = []

    async def tick(settings):
        started.append(asyncio.get_running_loop().time())

    async def run():
        t0 = asyncio.get_running_loop().time()
        await sweeps.run_lease_sweep(
            lambda: object(), interval_s=5, tick_timeout_s=1, tick=tick, max_ticks=1
        )
        return t0

    t0 = asyncio.run(run())
    assert started and started[0] - t0 < 0.5


def test_one_tick_runs_the_three_sweeps_off_the_loop(monkeypatch):
    """The tick calls exactly the three sweeps, and the two synchronous ones
    through `asyncio.to_thread` (the AST guard pins the source; this pins
    the behaviour: they run, and their results are reported)."""
    calls = []
    monkeypatch.setattr(db, "requeue_expired_claims", lambda s: calls.append("requeue") or 2)

    async def _reconcile(s):
        calls.append("reconcile")
        return 1

    monkeypatch.setattr(reconcile, "reconcile_pushed_expired_claims", _reconcile)
    monkeypatch.setattr(
        db,
        "reap_expired_release_preps",
        lambda s: calls.append("reap")
        or [{"version": "v1", "worker": "w", "held_minutes": 3}],
    )
    out = asyncio.run(sweeps.lease_sweep_tick(object()))
    assert calls == ["requeue", "reconcile", "reap"]
    assert out == {"requeued": 2, "reconciled": 1, "reaped": 1}


# ---------------------------------------------------------------- AC4


def test_the_comments_no_longer_promise_no_scheduler():
    """The remarks that said the listing was self-healing *without* a
    scheduler are gone; a future reader is pointed at the sweep."""
    import inspect
    import pathlib

    worker_src = pathlib.Path(worker_router.__file__).read_text(encoding="utf-8")
    db_src = pathlib.Path(db.__file__).read_text(encoding="utf-8")
    assert "self-healing without a background scheduler" not in worker_src
    assert "self-healing without a background\n    scheduler" not in db_src
    assert "sweeps.py" in inspect.getdoc(db.list_worker_pool)
    assert "sweeps.py" in inspect.getdoc(db.requeue_expired_claims)
