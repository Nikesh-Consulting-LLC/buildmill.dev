"""Phase 103: a release prep cannot be abandoned in silence.

The 2026.08.16.3 incident: the runner restarted ten minutes into preparing a
release, its supervising task died, and the `release_prep_runs` row did not.
Two and a half hours later the prep was still `running` with an expired lease
and a healthy worker online — because release prep was the one claimed job in
the factory with no lease reaper, and the runner's pool query asks for
`queued`, so the job it held was invisible to it precisely because it held it.

Endpoint-level; the reaper's SQL is covered in
test_release_prep_reaper_sql.py.
"""

import uuid

import pytest

from app import release_prep

ORG_ID = str(uuid.uuid4())
PROJECT_ID = str(uuid.uuid4())
PREP_ID = str(uuid.uuid4())
RELEASE_ID = str(uuid.uuid4())

WORKER = {
    "id": str(uuid.uuid4()),
    "org_id": ORG_ID,
    "name": "Architect",
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


@pytest.fixture(autouse=True)
def stub_briefing(monkeypatch):
    """The briefing reads the project's live instruction files; stub it so the
    endpoint tests stay database-free."""
    monkeypatch.setattr(
        release_prep,
        "briefing",
        lambda settings, prep_id, worker: {
            "instruction": "Write the notes.",
            "agent_instructions": "",
            "notes_vocabulary": "sections: ...",
        },
    )


# --- US-103.2: the runner asks what it already holds ------------------------


def test_held_lists_only_this_workers_running_preps(client, worker_auth, monkeypatch):
    seen = {}

    def fake_held(settings, worker_id, org_id):
        seen["args"] = (worker_id, org_id)
        return [
            {
                "id": PREP_ID,
                "release_id": RELEASE_ID,
                "project_id": PROJECT_ID,
                "project_name": "Demo",
                "repo_full_name": "acme/demo",
                "version": "2026.08.16.3",
                "commit_sha": "a4ee291",
                "claimed_at": "2026-08-16T13:36:16+00:00",
                "claim_expires_at": "2026-08-16T15:46:00+00:00",
            }
        ]

    monkeypatch.setattr("app.routers.worker.db.list_held_release_preps", fake_held)
    resp = client.get("/api/v1/worker/release-prep/held", headers=HDR)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["version"] == "2026.08.16.3"
    # Scoped to the caller's own worker id and org — never another worker's
    # claim, whatever the token can otherwise see.
    assert seen["args"] == (WORKER["id"], ORG_ID)


def test_held_carries_the_same_briefing_a_claim_does(client, worker_auth, monkeypatch):
    """Re-adoption must run the job exactly as claiming it would. If the
    briefing rode only on the claim, a re-adopted prep would run with no
    Release instruction — the very steering us-101.6 existed to deliver."""
    monkeypatch.setattr(
        "app.routers.worker.db.list_held_release_preps",
        lambda s, w, o: [
            {
                "id": PREP_ID,
                "release_id": RELEASE_ID,
                "project_id": PROJECT_ID,
                "project_name": "Demo",
                "repo_full_name": "acme/demo",
                "version": "2026.08.16.3",
                "commit_sha": "a4ee291",
                "claimed_at": None,
                "claim_expires_at": None,
            }
        ],
    )
    item = client.get("/api/v1/worker/release-prep/held", headers=HDR).json()["items"][0]
    assert item["instruction"] == "Write the notes."
    assert item["notes_vocabulary"]


def test_held_is_empty_when_nothing_is_held(client, worker_auth, monkeypatch):
    monkeypatch.setattr(
        "app.routers.worker.db.list_held_release_preps", lambda s, w, o: []
    )
    resp = client.get("/api/v1/worker/release-prep/held", headers=HDR)
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_held_needs_a_worker_token(client):
    assert client.get("/api/v1/worker/release-prep/held").status_code in (401, 403)


def test_held_is_not_swallowed_by_the_prep_id_route(client, worker_auth, monkeypatch):
    """FastAPI matches in order: registered after /release-prep/{prep_id},
    the literal path would never be reached and 'held' would be read as a
    prep id."""
    monkeypatch.setattr(
        "app.routers.worker.db.list_held_release_preps", lambda s, w, o: []
    )
    resp = client.get("/api/v1/worker/release-prep/held", headers=HDR)
    assert resp.status_code == 200
    assert "items" in resp.json()


# --- US-103.1/103.3: what a prep says once it is no longer running ----------


@pytest.mark.parametrize(
    ("status", "needle"),
    [
        ("cancelled", "the manager stopped this release"),
        ("failed", "its claim expired"),
        ("succeeded", "already been submitted"),
        ("queued", "not claimed"),
    ],
)
def test_a_prep_that_is_not_running_says_why(status, needle):
    """This refusal is read in an agent's transcript, and it is the only place
    the agent learns the job was taken away. "is failed, not running" told
    nobody anything and invited a retry loop."""
    message = release_prep.not_running_error(status)
    assert needle in message
    if status in ("cancelled", "failed"):
        assert "Do not retry" in message


def test_an_unknown_status_still_answers():
    assert "not running" in release_prep.not_running_error("something-new")


def test_heartbeat_on_a_stopped_prep_explains_itself(client, worker_auth, monkeypatch):
    """us-103.3: an agent whose release was stopped under it learns why on its
    next beat, rather than reading "no live claim to extend"."""
    monkeypatch.setattr(
        "app.routers.worker.db.heartbeat_release_prep", lambda s, p, w: False
    )
    monkeypatch.setattr(
        "app.routers.worker.db.get_release_prep",
        lambda s, p, o: {"id": PREP_ID, "status": "cancelled"},
    )
    resp = client.post(f"/api/v1/worker/release-prep/{PREP_ID}/heartbeat", headers=HDR)
    assert resp.status_code == 409
    assert "the manager stopped this release" in resp.json()["detail"]


def test_heartbeat_still_says_no_live_claim_when_that_is_the_truth(
    client, worker_auth, monkeypatch
):
    monkeypatch.setattr(
        "app.routers.worker.db.heartbeat_release_prep", lambda s, p, w: False
    )
    monkeypatch.setattr("app.routers.worker.db.get_release_prep", lambda s, p, o: None)
    resp = client.post(f"/api/v1/worker/release-prep/{PREP_ID}/heartbeat", headers=HDR)
    assert resp.status_code == 409
    assert "no live claim" in resp.json()["detail"]


def test_a_stopped_prep_refuses_a_full_valid_submit(monkeypatch):
    """US-103.3 AC3: the zombie case. A stopped agent coming back with a
    complete payload must write NOTHING — no notes, no cases, no document, no
    UAT deploy."""
    import asyncio

    from app import db

    monkeypatch.setattr(
        db,
        "get_release_prep",
        lambda s, p, o: {
            "id": PREP_ID,
            "status": "cancelled",
            "worker_id": WORKER["id"],
            "project_id": PROJECT_ID,
            "release_id": RELEASE_ID,
        },
    )

    def explode(*a, **k):  # any write at all is the bug
        raise AssertionError("a stopped prep wrote to the release")

    monkeypatch.setattr(db, "update_release", explode)
    monkeypatch.setattr(db, "attach_release_test_cases", explode)
    monkeypatch.setattr(db, "stamp_release_milestones", explode)
    monkeypatch.setattr(db, "complete_release_prep", explode)

    result = asyncio.run(
        release_prep.submit(
            None,
            PREP_ID,
            WORKER,
            notes_summary="# Release 2026.08.16.3",
            notes_detail="everything changed",
        )
    )
    assert "the manager stopped this release" in result["error"]


# ---------------------------------------------------------------------------
# us-117.3 — a prep resolves its model like a run does
# ---------------------------------------------------------------------------


def test_the_claim_carries_a_model_resolved_from_the_org_floor(monkeypatch):
    """The agent pins nothing, the default preset names nothing, and the org's
    default LLM provider names `grok-4.6` — us-116.7's floor.

    The runner used to read only `model_overrides`, so this agent got no model
    at all and the interactive CLI refused: "this agent has no model to reason
    with … or set a default model on the org's default LLM provider" — advice
    the manager had already followed. DevOps failed exactly this way on 16, 17
    and 18 August."""
    from app import release_prep

    monkeypatch.setattr(
        release_prep.db, "get_runner_config",
        lambda s, w: {"enabled_kinds": ["test", "release", "deploy"],
                      "model_overrides": {}},
    )
    monkeypatch.setattr(release_prep.db, "presets_by_id", lambda s, o: {})
    monkeypatch.setattr(
        release_prep.db, "org_default_preset",
        lambda s, o: {"id": "preset-1", "name": "Balanced", "model": None,
                      "settings": {}, "version": 1, "tool_grants": []},
    )
    monkeypatch.setattr(
        release_prep.db, "org_default_provider_model", lambda s, o: "grok-4.6"
    )

    model = release_prep.resolved_model(
        object(), {"id": "w-1", "org_id": "org-1"}
    )
    assert model == "grok-4.6"


def test_the_agents_own_pin_still_wins(monkeypatch):
    """US-63.x's intent, unchanged: a manager who pinned a model for this
    agent gets that model, not the org's."""
    from app import release_prep

    monkeypatch.setattr(
        release_prep.db, "get_runner_config",
        lambda s, w: {"model_overrides": {"release": "grok-4.5"}},
    )
    monkeypatch.setattr(release_prep.db, "presets_by_id", lambda s, o: {})
    monkeypatch.setattr(release_prep.db, "org_default_preset", lambda s, o: None)
    monkeypatch.setattr(
        release_prep.db, "org_default_provider_model", lambda s, o: "grok-4.6"
    )

    assert release_prep.resolved_model(
        object(), {"id": "w-1", "org_id": "org-1"}
    ) == "grok-4.5"


def test_nobody_chose_anywhere_still_resolves_to_nothing(monkeypatch):
    """The refusal must survive: `None` here is what makes the runner say
    "nothing was spent" rather than starting on a model nobody picked."""
    from app import release_prep

    monkeypatch.setattr(
        release_prep.db, "get_runner_config", lambda s, w: {"model_overrides": {}}
    )
    monkeypatch.setattr(release_prep.db, "presets_by_id", lambda s, o: {})
    monkeypatch.setattr(release_prep.db, "org_default_preset", lambda s, o: None)
    monkeypatch.setattr(
        release_prep.db, "org_default_provider_model", lambda s, o: None
    )

    assert release_prep.resolved_model(
        object(), {"id": "w-1", "org_id": "org-1"}
    ) is None
