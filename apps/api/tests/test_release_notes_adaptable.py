"""us-113.1: the values a release-prep submit writes must be adaptable.

Every release prep crashed for a day after Phase 101 shipped. us-101.4 added
`notes_doc` to the update patch as a raw dict; psycopg has no dumper for
`dict`, so `update_release` raised `cannot adapt type 'dict'` client-side,
before any SQL was sent — Postgres logged nothing, and the agent read the
resulting 500 as if its payload were wrong and retried sixty-one times.

Every existing test of this path monkeypatches `db.update_release`, so the
adaptation that fails never runs. It does not need a database to check:
psycopg's adapter registry answers "can this be a parameter?" in-process, so
these tests run in the Essential suite.
"""

import asyncio
import json
import uuid

import psycopg
import pytest
from psycopg.adapt import PyFormat

from app import db, release_prep

ORG_ID = str(uuid.uuid4())
PROJECT_ID = str(uuid.uuid4())
PREP_ID = str(uuid.uuid4())
RELEASE_ID = str(uuid.uuid4())
VERSION = "2026.08.16.4"

WORKER = {"id": str(uuid.uuid4()), "org_id": ORG_ID, "name": "Programmer"}

# us-101.3: a release with no checks is refused, so every submit carries one.
CASES = [
    {
        "title": "Pre-flight: UAT health responds",
        "steps": "Open /health on the UAT deployment.",
        "expected_result": "HTTP 200.",
        "section": "pre-flight",
    }
]


def unadaptable(values) -> list[str]:
    """The values psycopg would refuse as query parameters."""
    bad = []
    for v in values:
        if v is None:
            continue
        try:
            psycopg.postgres.adapters.get_dumper(type(v), PyFormat.AUTO)
        except Exception as e:  # noqa: BLE001 — the message is the evidence
            bad.append(f"{type(v).__name__}: {e}")
    return bad


def test_psycopg_still_has_no_dumper_for_dict():
    """The premise. If psycopg ever grows one, the guard below is redundant
    rather than wrong — but this test says so out loud instead of leaving the
    next reader to wonder why the encoding exists."""
    assert unadaptable([{"standfirst": "hi"}])
    assert not unadaptable(["text", 3, True, ["a", "b"]])


# ------------------------------------------------ the write, without a database


class FakeCursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class FakeConn:
    def __init__(self):
        self.params: tuple = ()

    def execute(self, query, params=None):
        self.params = params or ()
        return FakeCursor({"id": RELEASE_ID})

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_update_release_encodes_a_dict_and_leaves_everything_else_alone(monkeypatch):
    conn = FakeConn()
    monkeypatch.setattr(db, "_connect", lambda s: conn)

    doc = {"standfirst": "what shipped", "sections": {"fixes": ["one"]}}
    db.update_release(
        None,
        RELEASE_ID,
        {
            "notes_summary": f"# Release {VERSION}",
            "notes_doc": doc,
            "status": "notes-ready",
        },
    )

    assert not unadaptable(conn.params), conn.params
    # AC4: it round-trips as the object it was, not as a quoted string.
    encoded = conn.params[1]
    assert isinstance(encoded, str)
    assert json.loads(encoded) == doc
    # The plain columns are untouched — no blanket json.dumps over the patch.
    assert conn.params[0] == f"# Release {VERSION}"
    assert conn.params[2] == "notes-ready"


def test_a_list_value_is_left_for_the_array_dumper(monkeypatch):
    """Lists are array columns; encoding them would break those writes."""
    conn = FakeConn()
    monkeypatch.setattr(db, "_connect", lambda s: conn)

    db.update_release(None, RELEASE_ID, {"included_items": ["US-1.1", "US-1.2"]})
    assert conn.params[0] == ["US-1.1", "US-1.2"]


# ----------------------------------------------- the whole submit path's patch


@pytest.fixture
def submitting(monkeypatch):
    """Drive release_prep.submit far enough to see the patches it builds.

    Plural: the submit updates the release again when the UAT deploy that
    follows it cannot run, and that patch has to be adaptable too."""
    captured: list[dict] = []

    monkeypatch.setattr(
        db,
        "get_release_prep",
        lambda s, p, o: {
            "id": PREP_ID,
            "status": "running",
            "worker_id": WORKER["id"],
            "project_id": PROJECT_ID,
            "release_id": RELEASE_ID,
        },
    )
    monkeypatch.setattr(
        db,
        "get_release",
        lambda s, r: {"id": RELEASE_ID, "version": VERSION, "included_items": []},
    )
    monkeypatch.setattr(db, "release_inheritable_display_ids", lambda *a, **k: [])
    monkeypatch.setattr(db, "attach_release_inherited_cases", lambda *a, **k: 0)
    monkeypatch.setattr(db, "attach_release_test_cases", lambda *a, **k: 0)
    monkeypatch.setattr(db, "stamp_release_milestones", lambda *a, **k: None)
    monkeypatch.setattr(db, "complete_release_prep", lambda *a, **k: None)
    monkeypatch.setattr(db, "get_release_uat_deployment_id", lambda *a, **k: None)

    def capture(settings, release_id, patch):
        captured.append(patch)
        return {"id": release_id}

    monkeypatch.setattr(db, "update_release", capture)
    return captured


def test_every_value_the_submit_path_writes_is_adaptable(submitting):
    """The regression itself: a submit carrying a notes_doc must not build a
    patch psycopg would refuse."""
    result = asyncio.run(
        release_prep.submit(
            None,
            PREP_ID,
            WORKER,
            notes_summary=f"# Release {VERSION}",
            notes_detail="schema unchanged; one module touched",
            test_cases=CASES,
            notes_doc={
                "standfirst": "what shipped",
                "sections": {"fixes": ["the release prep writes its notes"]},
            },
        )
    )
    assert "error" not in result, result

    notes = submitting[0]
    assert "notes_doc" in notes
    for patch in submitting:
        assert not unadaptable(patch.values()), patch


def test_the_patch_is_adaptable_when_the_agent_sends_no_doc(submitting):
    """`as_declaration` returns a dict either way — the empty case crashed too."""
    asyncio.run(
        release_prep.submit(
            None,
            PREP_ID,
            WORKER,
            notes_summary=f"# Release {VERSION}",
            notes_detail="schema unchanged",
            test_cases=CASES,
        )
    )
    for patch in submitting:
        assert not unadaptable(patch.values()), patch
