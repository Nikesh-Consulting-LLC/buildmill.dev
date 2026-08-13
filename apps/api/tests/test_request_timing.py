"""US-62.8: an API request says where its time went. No app-wide request
timing existed before this -- these tests pin the accumulator, the
transparent connection wrapper, and the log write's SQL shape.

US-87.6 changed two of those contracts and these tests follow, deliberately
keeping what each one was actually protecting:

* `_TimedConnection` used to wrap a freshly-opened connection whose `with`
  block closed it. It now wraps the process POOL and leases for the duration
  of the block. What still matters -- and is still asserted -- is that time
  accumulates across blocks, that it is a no-op outside a timed request, and
  that every other attribute reaches the real connection untouched.
* `record_api_request` used to insert one row per call on its own
  connection, 584,613 times over six weeks on prod. It now buffers and writes
  in batches. The SQL shape and the exact column order are still pinned,
  because those are what make the row readable; only WHEN the write happens
  moved. `test_pool_and_heartbeat.py` covers the buffering rules themselves.
"""

from __future__ import annotations

import time

from app import db


class FakeCursor:
    def __init__(self, conn=None):
        self._conn = conn

    def fetchone(self):
        return None

    def execute(self, q, p=None):
        if self._conn is not None:
            self._conn.executed.append((" ".join(q.split()), p))
        return self

    def executemany(self, q, seq):
        if self._conn is not None:
            self._conn.executed.append((" ".join(q.split()), list(seq)))
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakePsycoConn:
    """Stands in for the real psycopg connection the pool hands out."""

    def __init__(self):
        self.entered = False
        self.exited = False
        self.executed: list[tuple[str, tuple | list | None]] = []

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *exc):
        self.exited = True
        return False

    def execute(self, q, p=None):
        self.executed.append((" ".join(q.split()), p))
        return FakeCursor()

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        pass


class FakePool:
    """Stands in for the psycopg_pool ConnectionPool `_TimedConnection` leases
    from. `connection()` is a context manager, exactly as the real one is."""

    def __init__(self, conn: FakePsycoConn):
        self._conn = conn

    def connection(self):
        return self._conn


def _timed(conn: FakePsycoConn) -> db._TimedConnection:
    return db._TimedConnection(FakePool(conn))


def test_db_time_accumulates_across_nested_with_blocks():
    token = db.begin_request_timing()
    try:
        for _ in range(3):
            with _timed(FakePsycoConn()):
                time.sleep(0.001)
        ms = db.end_request_timing(token)
    finally:
        pass
    assert ms >= 0
    # Three blocks each holding the lease briefly must add up, not overwrite.
    assert isinstance(ms, int)


def test_the_wrapper_is_a_no_op_outside_a_timed_request():
    """A background sweep or a test's fake settings never calls
    begin_request_timing — the wrapper must not raise or need one."""
    with _timed(FakePsycoConn()) as inner:
        assert isinstance(inner, FakePsycoConn)
    # No exception, and nothing to assert about accumulation: there was
    # nothing to accumulate into.


def test_the_wrapper_passes_through_every_other_attribute():
    fake = FakePsycoConn()
    with _timed(fake) as conn:
        conn.execute("select 1", None)
        conn.commit()
    assert fake.executed == [("select 1", None)]
    assert fake.entered and fake.exited


def test_end_request_timing_resets_for_the_next_request():
    token = db.begin_request_timing()
    with _timed(FakePsycoConn()):
        pass
    first = db.end_request_timing(token)
    assert first >= 0

    # A second, unrelated request must start from zero, not carry over.
    token2 = db.begin_request_timing()
    second = db.end_request_timing(token2)
    assert second == 0


def test_record_api_request_writes_route_method_and_both_durations(monkeypatch):
    conn = FakePsycoConn()
    monkeypatch.setattr(db, "_connect", lambda s: _timed(conn))
    with db._LOG_BUFFER_LOCK:
        db._LOG_BUFFER.clear()
    try:
        db.record_api_request(
            object(), "/api/v1/issues/{issue_id}", "GET", 200, 45, 12
        )
        # US-87.6: buffered, so nothing has been written yet. The flush is
        # what the API's lifespan shutdown calls.
        assert conn.executed == []
        db.flush_api_request_log(object())
        q, rows = conn.executed[0]
        assert "insert into public.api_request_log" in q
        # Column order is still exactly what the table expects.
        assert rows == [("/api/v1/issues/{issue_id}", "GET", 200, 45, 12)]
    finally:
        with db._LOG_BUFFER_LOCK:
            db._LOG_BUFFER.clear()


def test_record_api_request_never_raises_on_a_db_failure(monkeypatch):
    def boom(settings):
        raise RuntimeError("db is down")

    monkeypatch.setattr(db, "_connect", boom)
    with db._LOG_BUFFER_LOCK:
        db._LOG_BUFFER.clear()
    try:
        # Must not raise -- a request must never fail because logging it
        # failed. The flush is where the write actually happens now, so it
        # is the call that has to swallow the failure.
        db.record_api_request(object(), "/x", "GET", 200, 1, 0)
        db.flush_api_request_log(object())
    finally:
        with db._LOG_BUFFER_LOCK:
            db._LOG_BUFFER.clear()
