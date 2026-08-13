"""US-52.4 → US-53.1: the billing stamp rides the same statement as the
resolved settings, and its value is decided by the CALLER from the agent's
config — the record's `billing` key, defaulting to `metered`."""

import uuid

from app import db


class _FakeCursor:
    def fetchone(self):
        return None


def _fake_connect(calls):
    class FakeConn:
        def execute(self, query, params=None):
            calls.append((query, params))
            return _FakeCursor()

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    return lambda s: FakeConn()


def _stamp(monkeypatch, record):
    calls = []
    monkeypatch.setattr(db, "_connect", _fake_connect(calls))
    db.record_run_settings(object(), str(uuid.uuid4()), record)
    assert len(calls) == 1
    query, params = calls[0]
    assert "billing" in query
    # billing rides second-to-last, ahead of the run id.
    return params[-2]


def test_callers_billing_is_stamped(monkeypatch):
    assert (
        _stamp(monkeypatch, {"resolved_settings": {}, "billing": "subscription"})
        == "subscription"
    )
    assert (
        _stamp(monkeypatch, {"resolved_settings": {}, "billing": "metered"})
        == "metered"
    )


def test_absent_billing_defaults_to_metered(monkeypatch):
    assert _stamp(monkeypatch, {"resolved_settings": {"model": "m"}}) == "metered"
    # US-53.1: a resolved `auth` value no longer flips anything — billing is
    # the caller's decision from the agent's config, not a resolved setting.
    assert (
        _stamp(monkeypatch, {"resolved_settings": {"auth": "subscription"}})
        == "metered"
    )
