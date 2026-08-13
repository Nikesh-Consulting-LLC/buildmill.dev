"""US-89.2: the environment is defined once — resolution and disclosure rules.

No database, no storage: list_entries and get_object are monkeypatched.
"""

import asyncio
import uuid

from app import project_env

ORG = str(uuid.uuid4())
PROJECT = str(uuid.uuid4())
AGENT = str(uuid.uuid4())


def _row(name, kind="plain", value=None, agent_id=None, fingerprint=None, desc=""):
    return {
        "id": str(uuid.uuid4()),
        "org_id": ORG,
        "project_id": PROJECT,
        "agent_id": agent_id,
        "name": name,
        "kind": kind,
        "value": value,
        "fingerprint": fingerprint,
        "description": desc,
    }


def _resolve(monkeypatch, rows, blobs=None):
    monkeypatch.setattr(
        project_env, "list_entries", lambda s, p, a=None: rows
    )

    async def fake_get_object(settings, path, bucket=None):
        return (blobs or {}).get(path)

    monkeypatch.setattr(project_env.storage, "get_object", fake_get_object)
    return asyncio.run(project_env.effective_env(None, PROJECT, AGENT))


def test_plain_values_deliver_and_agent_scope_wins(monkeypatch):
    rows = [
        _row("APP_URL", value="https://app.example.test"),
        _row("APP_URL", value="https://agent-override.example.test", agent_id=AGENT),
        _row("FLAG", value="on", desc="feature flag"),
    ]
    values, catalog = _resolve(monkeypatch, rows)
    # list_entries orders project-wide first; the scoped row overwrites.
    assert values["APP_URL"] == "https://agent-override.example.test"
    assert values["FLAG"] == "on"
    by_name = {c["name"]: c for c in catalog}
    assert by_name["APP_URL"]["scope"] == "agent"
    assert by_name["FLAG"]["description"] == "feature flag"


def test_secret_values_come_from_the_bucket(monkeypatch):
    row = _row("DB_PASSWORD", kind="secret", fingerprint="abcd1234")
    path = project_env.secret_path(ORG, PROJECT, row["id"])
    values, catalog = _resolve(
        monkeypatch, [row], blobs={path: b"hunter2-postgres"}
    )
    assert values["DB_PASSWORD"] == "hunter2-postgres"
    assert catalog[0]["set"] is True


def test_an_unset_secret_is_absent_not_empty(monkeypatch):
    rows = [_row("DB_PASSWORD", kind="secret", fingerprint=None)]
    values, catalog = _resolve(monkeypatch, rows)
    assert "DB_PASSWORD" not in values
    assert catalog[0]["set"] is False


def test_secret_values_for_run_filters_plain_and_short(monkeypatch):
    row = _row("DB_PASSWORD", kind="secret", fingerprint="abcd1234")
    path = project_env.secret_path(ORG, PROJECT, row["id"])
    values, catalog = _resolve(
        monkeypatch,
        [row, _row("FLAG", value="on")],
        blobs={path: b"hunter2-postgres"},
    )
    swept = project_env.secret_values_for_run(values, catalog)
    # Only the secret, never the plain flag (and never trivially short
    # strings that would false-positive all over a diff).
    assert swept == ["hunter2-postgres"]


def test_fingerprint_is_stable_and_short():
    assert project_env.fingerprint("hunter2") == project_env.fingerprint("hunter2")
    assert len(project_env.fingerprint("hunter2")) == 8
