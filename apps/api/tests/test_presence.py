"""us-116.4: presence has an expiry, and there is one predicate for it.

`disconnected_at is null` was "online" on six web surfaces and two API
readers; `last_seen_at` was heartbeated every 30 s and read by nothing; the
reaper migration 099 promised was never written. These pin the one window, the
sweep that enforces it, the heartbeat that revives a swept row, and the state
map every surface renders.
"""

from __future__ import annotations

import re
from pathlib import Path

from app import db

MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "infra" / "supabase" / "migrations" / "281_live_runner_sessions.sql"
)


class _Cursor:
    def __init__(self, rows=None, row=None):
        self._rows = rows or []
        self._row = row

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._row


class _Conn:
    def __init__(self, rows=None, row=None):
        self.calls: list[tuple[str, tuple]] = []
        self._rows = rows
        self._row = row

    def execute(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params))
        return _Cursor(self._rows, self._row)

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


WORKER = "11111111-1111-4111-8111-111111111111"


def test_the_view_and_the_api_agree_on_the_window():
    """One number, in SQL and in Python. If either moves without the other,
    the roster (view) and the sweep (API) disagree about who is online."""
    src = MIGRATION.read_text(encoding="utf-8")
    m = re.search(r"interval '(\d+) seconds'", src)
    assert m, "the view must state its window in seconds"
    assert int(m.group(1)) == db.PRESENCE_WINDOW_SECONDS == 90
    assert "security_invoker = true" in src, "the view must run under the caller's RLS"


def test_the_sweep_closes_only_rows_past_the_window(monkeypatch):
    conn = _Conn(rows=[{"id": "s1"}, {"id": "s2"}])
    monkeypatch.setattr(db, "_connect", lambda settings: conn)
    assert db.close_stale_runner_sessions(object()) == 2
    sql, params = conn.calls[0]
    assert "set disconnected_at = now()" in sql
    assert "disconnected_at is null" in sql
    assert "last_seen_at < now() - make_interval(secs => %s)" in sql
    assert params == (db.PRESENCE_WINDOW_SECONDS,)


def test_a_heartbeat_revives_a_swept_row(monkeypatch):
    """A false-positive sweep must self-heal on the next beat, or one delayed
    beat leaves a live agent reading Offline until it reconnects."""
    conn = _Conn()
    monkeypatch.setattr(db, "_connect", lambda settings: conn)
    db.touch_runner_session(object(), "sess-1")
    sql, params = conn.calls[0]
    assert "set last_seen_at = now(), disconnected_at = null" in sql
    assert "where id = %s" in sql and "disconnected_at is null" not in sql.split("where")[1]
    assert params == ("sess-1",)


def test_presence_reads_the_view_not_the_table(monkeypatch):
    conn = _Conn(row={"?column?": 1})
    monkeypatch.setattr(db, "_connect", lambda settings: conn)
    assert db.worker_is_live(object(), WORKER) is True
    sql, _ = conn.calls[0]
    assert "from public.live_runner_sessions" in sql
    assert "disconnected_at" not in sql


def test_agent_status_puts_offline_first_and_maps_the_two_words(monkeypatch):
    monkeypatch.setattr(db, "worker_is_live", lambda s, w: False)
    monkeypatch.setattr(db, "worker_last_seen", lambda s, w: None)
    monkeypatch.setattr(db, "worker_idle_reason", lambda s, w: {"reason": "revoked", "detail": "x"})
    assert db.agent_status(object(), WORKER)["state"] == "offline"

    monkeypatch.setattr(db, "worker_is_live", lambda s, w: True)
    for word, state in (
        ("revoked", "revoked"), ("working", "working"), ("paused", "stopped"),
        ("no-roles", "no-roles"), ("no-model", "no-model"), ("no-grants", "no-grants"),
        ("queue-held", "queue-held"), ("idle", "ready"),
    ):
        monkeypatch.setattr(db, "worker_idle_reason", lambda s, w, word=word: {"reason": word, "detail": "d"})
        out = db.agent_status(object(), WORKER)
        assert out["state"] == state, word
        assert out["reason"] == word
        assert out["state"] in db.AGENT_STATES


def test_no_web_source_reads_disconnected_at_directly():
    """The one-predicate rule, enforced: `apps/web/src` reads presence through
    the `live_runner_sessions` view, never the table's column."""
    web = Path(__file__).resolve().parents[3] / "apps" / "web" / "src"
    offenders = sorted(
        str(p.relative_to(web)).replace("\\", "/")
        for p in web.rglob("*.ts*")
        if p.name != "database.types.ts" and "disconnected_at" in p.read_text(encoding="utf-8")
    )
    assert offenders == [], offenders


def test_the_api_reads_presence_through_the_view_or_the_helper():
    """The API's own readers: only `db.py` — the writers (open/close/touch/
    sweep) and the `worker_is_live` helper — may name `disconnected_at`; every
    other module reads presence through the view or the helper."""
    app_dir = Path(db.__file__).resolve().parent
    offenders = sorted(
        str(p.relative_to(app_dir)).replace("\\", "/")
        for p in app_dir.rglob("*.py")
        if p.name != "db.py" and "disconnected_at" in p.read_text(encoding="utf-8")
    )
    assert offenders == [], offenders
