"""US-21.1: cutting a release — preview, guards, pinning and versioning.

GitHub and PostgREST are stubbed; these cover what the endpoint decides, not
a live repo.
"""

import uuid

import pytest

from app import releases as releases_mod

PROJECT_ID = str(uuid.uuid4())
ORG_ID = str(uuid.uuid4())
HEAD_SHA = "f" * 40


def _auth(make_token, **claims):
    return {"Authorization": f"Bearer {make_token(**claims)}"}


def _project(**over):
    return {
        "id": PROJECT_ID,
        "org_id": ORG_ID,
        "name": "Demo",
        "repo_full_name": "acme/demo",
        "default_branch": "main",
        "release_uat_deployment_id": str(uuid.uuid4()),
        "release_prod_deployment_id": None,
        **over,
    }


def _wire(
    monkeypatch,
    *,
    project=None,
    in_flight=None,
    prev=None,
    runs=None,
    issues=None,
    commits=None,
):
    """Stub the four tables build_preview reads plus the GitHub calls."""
    tables = {
        "projects": [project or _project()],
        "releases": [],
        "runs": runs or [],
        "issues": issues or [],
    }

    async def fake_get(settings, token, table, params):
        if table == "releases":
            status = params.get("status", "")
            if status.startswith("in."):
                return in_flight or []
            if status == "eq.released":
                return [prev] if prev else []
            return []
        return tables.get(table, [])

    async def fake_rpc(settings, token, fn, args):
        assert fn == "next_release_version"
        return "2026.07.25.1"

    async def fake_token(settings, token, org_id, repo_full):
        return "gh-token"

    async def fake_branch(token, owner, repo, branch):
        return {"commit": {"sha": HEAD_SHA}}

    async def fake_compare(token, owner, repo, base, head):
        return {"commits": [{"sha": s} for s in (commits or [])]}

    async def fake_list(token, owner, repo, branch, limit=250):
        return [{"sha": s} for s in (commits or [])]

    monkeypatch.setattr(releases_mod, "postgrest_get", fake_get)
    monkeypatch.setattr(releases_mod, "rpc", fake_rpc)
    monkeypatch.setattr(
        releases_mod.github_tokens, "token_for_user", fake_token
    )
    monkeypatch.setattr(releases_mod.github, "get_branch", fake_branch)
    monkeypatch.setattr(releases_mod.github, "compare_commits", fake_compare)
    monkeypatch.setattr(releases_mod.github, "list_branch_commits", fake_list)


def test_preview_requires_auth(client):
    resp = client.get(f"/api/v1/projects/{PROJECT_ID}/releases/preview")
    assert resp.status_code == 401


def test_preview_pins_the_branch_head_and_proposes_a_date_version(
    client, make_token, monkeypatch
):
    _wire(monkeypatch, commits=["c1"])
    resp = client.get(
        f"/api/v1/projects/{PROJECT_ID}/releases/preview", headers=_auth(make_token)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["commit_sha"] == HEAD_SHA
    assert body["version"] == "2026.07.25.1"
    assert body["first_release"] is True
    assert body["blockers"] == []


def test_preview_blocks_without_a_designated_uat_deployment(
    client, make_token, monkeypatch
):
    _wire(
        monkeypatch,
        project=_project(release_uat_deployment_id=None),
        commits=["c1"],
    )
    body = client.get(
        f"/api/v1/projects/{PROJECT_ID}/releases/preview", headers=_auth(make_token)
    ).json()
    assert any("UAT deployment" in b for b in body["blockers"])


def test_preview_blocks_while_a_release_is_in_flight(
    client, make_token, monkeypatch
):
    _wire(
        monkeypatch,
        in_flight=[{"id": "r1", "version": "2026.07.24.1", "status": "uat-deployed"}],
        commits=["c1"],
    )
    body = client.get(
        f"/api/v1/projects/{PROJECT_ID}/releases/preview", headers=_auth(make_token)
    ).json()
    assert any("still in flight" in b for b in body["blockers"])


def test_preview_blocks_when_nothing_merged_since_the_last_release(
    client, make_token, monkeypatch
):
    _wire(
        monkeypatch,
        prev={"id": "r0", "version": "2026.07.24.1", "commit_sha": "a" * 40},
        commits=[],
    )
    body = client.get(
        f"/api/v1/projects/{PROJECT_ID}/releases/preview", headers=_auth(make_token)
    ).json()
    assert any("nothing to release" in b for b in body["blockers"])
    assert body["first_release"] is False


def test_preview_resolves_items_from_run_merge_commits(
    client, make_token, monkeypatch
):
    issue_id = str(uuid.uuid4())
    _wire(
        monkeypatch,
        commits=["sha-in-range"],
        runs=[{"merge_commit_sha": "sha-in-range", "issue_id": issue_id}],
        issues=[
            {
                "id": issue_id,
                "title": "Add CSV export",
                "type": "story",
                "item_no": 4,
                "sub_no": 2,
                "epics": {"number": 3},
            }
        ],
    )
    body = client.get(
        f"/api/v1/projects/{PROJECT_ID}/releases/preview", headers=_auth(make_token)
    ).json()
    assert body["items"] == [
        {
            "issue_id": issue_id,
            "title": "Add CSV export",
            "type": "story",
            "display_id": "US-3.4.2",
        }
    ]


def test_first_release_reports_a_capped_history(client, make_token, monkeypatch):
    _wire(
        monkeypatch,
        commits=[f"c{i}" for i in range(releases_mod.FIRST_RELEASE_COMMIT_CAP)],
    )
    body = client.get(
        f"/api/v1/projects/{PROJECT_ID}/releases/preview", headers=_auth(make_token)
    ).json()
    # A range that claims completeness it does not have is the failure mode
    # this flag exists to prevent.
    assert body["truncated"] is True


def test_cut_refuses_when_the_preview_is_blocked(client, make_token, monkeypatch):
    _wire(
        monkeypatch,
        project=_project(release_uat_deployment_id=None),
        commits=["c1"],
    )
    resp = client.post(
        f"/api/v1/projects/{PROJECT_ID}/releases",
        json={},
        headers=_auth(make_token),
    )
    assert resp.status_code == 409
    assert "UAT deployment" in resp.json()["detail"]


@pytest.mark.parametrize(
    "row,expected",
    [
        ({"epic_number": 3, "item_no": 4, "sub_no": 2, "type": "story"}, "US-3.4.2"),
        ({"epic_number": 3, "item_no": 4, "sub_no": None, "type": "feature"}, "FEAT-3.4"),
        ({"epic_number": 1, "item_no": 9, "sub_no": None, "type": "bug"}, "BUG-1.9"),
        ({"epic_number": None, "item_no": 4, "sub_no": None, "type": "story"}, None),
    ],
)
def test_display_id(row, expected):
    assert releases_mod.display_id(row) == expected


# --- US-21.3: the release run is queued when the release is cut ------------


def _wire_cut(monkeypatch, dispatch_result, *, refs=None, branch_error=None):
    """A preview with no blockers, a successful insert, and a stubbed
    dispatch — the GitHub tag and branch paths are stubbed out too.

    `refs` collects the branches the cut creates (us-50.4); pass
    `branch_error` to make branch creation fail.
    """
    _wire(monkeypatch, commits=["c1"])
    created = {"id": str(uuid.uuid4()), "version": "2026.07.25.1",
               "commit_sha": HEAD_SHA, "status": "queued"}

    async def fake_post(settings, token, table, payload):
        return [created | payload]

    async def fake_patch(settings, token, table, where, patch):
        return []

    async def fake_tag(token, owner, repo, tag, sha):
        return None

    async def fake_token(settings, token, org_id, repo_full):
        return "gh-token"

    async def fake_get_ref(token, owner, repo, name):
        return None  # the release branch does not exist yet

    async def fake_create_ref(token, owner, repo, name, sha):
        if branch_error:
            raise branch_error
        if refs is not None:
            refs.append((name, sha))

    from app.routers import projects as projects_router

    monkeypatch.setattr(projects_router, "postgrest_post", fake_post)
    monkeypatch.setattr(projects_router, "postgrest_patch", fake_patch)
    monkeypatch.setattr(projects_router.github, "create_tag", fake_tag)
    monkeypatch.setattr(projects_router.github, "get_ref", fake_get_ref)
    monkeypatch.setattr(projects_router.github, "create_ref", fake_create_ref)
    monkeypatch.setattr(
        projects_router.github_tokens, "token_for_user", fake_token
    )
    monkeypatch.setattr(
        projects_router.db, "dispatch_release_prep_for",
        lambda s, rid, org: dispatch_result,
    )
    return created


def test_cutting_a_release_queues_its_run(client, make_token, monkeypatch):
    run_id = str(uuid.uuid4())
    _wire_cut(monkeypatch, {"run_id": run_id, "resume": False})
    resp = client.post(
        f"/api/v1/projects/{PROJECT_ID}/releases", json={}, headers=_auth(make_token)
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["run_id"] == run_id
    assert body["dispatch_error"] is None
    assert body["commit_sha"] == HEAD_SHA


def test_a_dispatch_failure_does_not_lose_the_release(
    client, make_token, monkeypatch
):
    """The release exists and is re-dispatchable once the reason is fixed —
    usually a UAT deployment that is missing or agent-forbidden."""
    _wire_cut(
        monkeypatch,
        {"error": "protected deployments are human-only — agents may never run them"},
    )
    resp = client.post(
        f"/api/v1/projects/{PROJECT_ID}/releases", json={}, headers=_auth(make_token)
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["run_id"] is None
    assert "human-only" in body["dispatch_error"]


def _wire_redispatch(monkeypatch):
    """The redispatch endpoint resolves the org through projects.py's own
    postgrest_get, not releases.py's."""
    _wire(monkeypatch, commits=["c1"])
    from app.routers import projects as projects_router

    async def fake_get(settings, token, table, params):
        return [{"org_id": ORG_ID}]

    monkeypatch.setattr(projects_router, "postgrest_get", fake_get)
    return projects_router


def test_redispatch_maps_a_refusal_to_409(client, make_token, monkeypatch):
    projects_router = _wire_redispatch(monkeypatch)

    monkeypatch.setattr(
        projects_router.db, "dispatch_release_prep_for",
        lambda s, rid, org: {"error": "a release run is already queued or running"},
    )
    resp = client.post(
        f"/api/v1/projects/{PROJECT_ID}/releases/{uuid.uuid4()}/dispatch",
        headers=_auth(make_token),
    )
    assert resp.status_code == 409
    assert "already queued" in resp.json()["detail"]


def test_redispatch_reports_resume(client, make_token, monkeypatch):
    """The mitigation for the single-run shape: a retry after a failed deploy
    resumes rather than rewriting notes that are already stored."""
    projects_router = _wire_redispatch(monkeypatch)

    monkeypatch.setattr(
        projects_router.db, "dispatch_release_prep_for",
        lambda s, rid, org: {"run_id": str(uuid.uuid4()), "resume": True},
    )
    resp = client.post(
        f"/api/v1/projects/{PROJECT_ID}/releases/{uuid.uuid4()}/dispatch",
        headers=_auth(make_token),
    )
    assert resp.status_code == 202
    assert resp.json()["resume"] is True


# --- US-23.1: cancel a release that hasn't started -------------------------


RELEASE_ID = str(uuid.uuid4())
RUN_ID = str(uuid.uuid4())


def _wire_cancel(monkeypatch, *, status="queued", runs=None):
    """A release plus whatever release runs exist for it."""
    from app.routers import releases as rel_router

    deleted = []
    patched = {}
    # us-103.3: Stop touches two tables now — the release AND the job — so the
    # harness has to be able to tell them apart.
    patches = []

    async def fake_get(settings, token, table, params):
        if table == "releases":
            return [{
                "id": RELEASE_ID, "org_id": ORG_ID, "project_id": PROJECT_ID,
                "version": "2026.07.25.1", "status": status,
                "commit_sha": HEAD_SHA,
            }]
        if table == "release_prep_runs":
            return runs if runs is not None else []
        return []

    async def fake_delete(settings, token, table, where):
        deleted.append((table, where))
        return []

    async def fake_patch(settings, token, table, where, payload):
        patches.append((table, payload))
        if table == "releases":
            patched.update(payload)
        return []

    monkeypatch.setattr(rel_router, "postgrest_get", fake_get)
    monkeypatch.setattr(rel_router, "postgrest_delete", fake_delete)
    monkeypatch.setattr(rel_router, "postgrest_patch", fake_patch)
    return {"deleted": deleted, "patched": patched, "patches": patches}


def test_cancel_removes_the_queued_run_and_marks_it_cancelled(
    client, make_token, monkeypatch
):
    captured = _wire_cancel(
        monkeypatch, runs=[{"id": RUN_ID, "status": "queued", "worker_id": None}]
    )
    resp = client.post(f"/api/v1/releases/{RELEASE_ID}/cancel", headers=_auth(make_token))
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"
    # The run is deleted, not marked failed: nothing happened, and a fabricated
    # failure would land in the activity feed.
    assert captured["deleted"] == [("release_prep_runs", {"id": f"eq.{RUN_ID}"})]
    assert captured["patched"]["status"] == "cancelled"


def test_stop_ends_the_running_prep_as_well_as_the_release(
    client, make_token, monkeypatch
):
    """US-103.3: the half that makes Stop safe.

    Before this, `running` was refused outright — which is how release
    2026.08.16.3 came to be cleared by editing the production database. Now it
    stops, and the JOB stops with it: the prep row moves to `cancelled`, so
    `release_prep.submit` (which requires `running`) refuses a zombie agent
    that comes back with notes.
    """
    captured = _wire_cancel(
        monkeypatch,
        status="running",
        runs=[{"id": RUN_ID, "status": "running", "worker_id": "w-1"}],
    )
    resp = client.post(
        f"/api/v1/releases/{RELEASE_ID}/cancel",
        headers=_auth(make_token),
        json={"comment": "runner died"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "cancelled"
    assert body["runs_stopped"] == 1 and body["runs_removed"] == 0
    # Not deleted: it happened, it cost a session, and the attempt list says so.
    assert captured["deleted"] == []
    prep_patch = next(p for t, p in captured["patches"] if t == "release_prep_runs")
    assert prep_patch["status"] == "cancelled"
    assert "stopped by" in prep_patch["error"] and "runner died" in prep_patch["error"]
    assert captured["patched"]["status"] == "cancelled"
    assert captured["patched"]["rejected_reason"] == "runner died"


def test_stop_ends_a_claimed_but_still_queued_run(client, make_token, monkeypatch):
    """worker_id set with status still queued is a claim in progress — it is
    ended rather than deleted, because someone may already be holding it."""
    captured = _wire_cancel(
        monkeypatch, runs=[{"id": RUN_ID, "status": "queued", "worker_id": "w-1"}]
    )
    resp = client.post(f"/api/v1/releases/{RELEASE_ID}/cancel", headers=_auth(make_token))
    assert resp.status_code == 200
    assert resp.json()["runs_stopped"] == 1
    assert captured["deleted"] == []


@pytest.mark.parametrize(
    "status", ["queued", "running", "notes-ready", "uat-deploy-failed"]
)
def test_stop_accepts_every_state_that_needs_it(
    client, make_token, monkeypatch, status
):
    _wire_cancel(monkeypatch, status=status)
    resp = client.post(f"/api/v1/releases/{RELEASE_ID}/cancel", headers=_auth(make_token))
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"


@pytest.mark.parametrize(
    ("status", "guidance"),
    [
        ("deploying", "let it finish"),
        ("uat-deployed", "reject it if testing found it bad"),
        ("uat-signed-off", "reject it if you no longer want it"),
        ("promoting", "roll it back"),
        ("released", "roll it back"),
        ("rejected", "already rejected"),
        ("rolled-back", "already rolled back"),
        ("cancelled", "already stopped"),
        ("failed", "retry it, or cut a new one"),
    ],
)
def test_stop_refused_elsewhere_names_the_action_that_applies(
    client, make_token, monkeypatch, status, guidance
):
    """A refusal that only says no is what sent the manager to the database."""
    _wire_cancel(monkeypatch, status=status)
    resp = client.post(f"/api/v1/releases/{RELEASE_ID}/cancel", headers=_auth(make_token))
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert f"release is {status}" in detail
    assert guidance in detail


def test_reject_stops_the_prep_it_used_to_leave_running(
    client, make_token, monkeypatch
):
    """US-103.3 AC5: rejecting left the JOB alive, so a zombie agent could
    write notes onto a rejected release and fire its UAT deploy."""
    captured = _wire_cancel(
        monkeypatch,
        status="running",
        runs=[{"id": RUN_ID, "status": "running", "worker_id": "w-1"}],
    )
    resp = client.post(
        f"/api/v1/releases/{RELEASE_ID}/reject",
        headers=_auth(make_token),
        json={"comment": "bad build"},
    )
    assert resp.status_code == 200
    prep_patch = next(p for t, p in captured["patches"] if t == "release_prep_runs")
    assert prep_patch["status"] == "cancelled"
    assert "bad build" in prep_patch["error"]
    assert captured["patched"]["status"] == "rejected"


# --- US-50.4: cutting also cuts release/<version> ---------------------------
#
# An external environment is deployed FROM a branch, because the other
# system's trigger is "something landed on prod". The cut already tags the
# pinned commit; a branch is the same act with a name that system can watch —
# and it is the pin, so a promotion ships what UAT tested even after main
# moved.


def test_cutting_a_release_creates_the_release_branch(
    client, make_token, monkeypatch
):
    refs: list = []
    _wire_cut(monkeypatch, {"run_id": str(uuid.uuid4()), "resume": False}, refs=refs)
    resp = client.post(
        f"/api/v1/projects/{PROJECT_ID}/releases", json={}, headers=_auth(make_token)
    )
    assert resp.status_code == 201
    body = resp.json()
    assert refs == [("release/2026.07.25.1", HEAD_SHA)]
    assert body["release_branch"] == "release/2026.07.25.1"
    assert body["branch_error"] is None


def test_a_branch_failure_is_reported_not_fatal(client, make_token, monkeypatch):
    """Its failure discipline copies the tag's exactly — the release row is
    the record, the branch is a convenience on top of it."""
    from app.github import GitHubError

    _wire_cut(
        monkeypatch,
        {"run_id": str(uuid.uuid4()), "resume": False},
        branch_error=GitHubError("could not create branch: Resource protected"),
    )
    resp = client.post(
        f"/api/v1/projects/{PROJECT_ID}/releases", json={}, headers=_auth(make_token)
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["commit_sha"] == HEAD_SHA  # the release still exists
    assert "Resource protected" in body["branch_error"]
    assert body["release_branch"] is None


def test_an_existing_release_branch_is_not_a_failure(client, make_token, monkeypatch):
    """Re-cutting after a rejected release must not trip over a branch that
    is already there."""
    refs: list = []
    _wire_cut(monkeypatch, {"run_id": str(uuid.uuid4()), "resume": False}, refs=refs)

    from app.routers import projects as projects_router

    async def existing(token, owner, repo, name):
        return {"ref": f"refs/heads/{name}"}

    monkeypatch.setattr(projects_router.github, "get_ref", existing)

    resp = client.post(
        f"/api/v1/projects/{PROJECT_ID}/releases", json={}, headers=_auth(make_token)
    )
    assert resp.status_code == 201
    assert resp.json()["branch_error"] is None
    assert refs == []  # nothing re-created


def test_release_branch_name_is_derived_from_the_version():
    assert releases_mod.release_branch_name("2026.07.25.1") == "release/2026.07.25.1"


# --- US-50.4: promotion carries the release branch --------------------------


def _wire_promote(monkeypatch, *, dep, dispatched, refusal=None):
    release = {
        "id": str(uuid.uuid4()),
        "org_id": ORG_ID,
        "project_id": PROJECT_ID,
        "status": "uat-signed-off",
        "version": "2026.07.25.1",
        "commit_sha": HEAD_SHA,
    }

    async def fake_get(settings, token, table, params):
        if table == "releases":
            return [release]
        if table == "projects":
            return [{"id": PROJECT_ID, "org_id": ORG_ID,
                     "release_prod_deployment_id": "dep-x"}]
        return []

    async def fake_patch(settings, token, table, where, patch):
        return []

    from app.routers import releases as releases_router

    monkeypatch.setattr(releases_router, "postgrest_get", fake_get)
    monkeypatch.setattr(releases_router, "postgrest_patch", fake_patch)
    monkeypatch.setattr(
        releases_router.db, "get_deployment_for_agent",
        lambda s, d, o: {"deployment": dep, "server": None,
                         "project": {"repo_full_name": "acme/demo", "name": "Demo"}},
    )
    monkeypatch.setattr(
        releases_router.db, "agent_deploy_refusal", lambda d: refusal
    )
    monkeypatch.setattr(
        releases_router.db, "dispatch_deploy_run",
        lambda s, dep_id, org, ref=None, auto_rollback=False, actor=None,
        release_branch=None: dispatched.update(
            {"ref": ref, "release_branch": release_branch}
        ) or {"run_id": "run-p1"},
    )
    monkeypatch.setattr(
        releases_router.deploy, "create_run",
        lambda s, d, by, email, source="branch", zip_filename=None,
        branch_override=None: "dr-1",
    )
    monkeypatch.setattr(
        releases_router.deploy, "launch",
        lambda s, ctx: dispatched.update({"ctx": ctx}),
    )
    return release


EXTERNAL_PROD = {
    "id": "dep-x",
    "org_id": ORG_ID,
    "name": "production",
    "kind": "external",
    "branch": "main",
    "target_branch": "prod",
    "environment": "production",
    "server_id": None,
    "protected": False,
    "agent_dispatch_allowed": True,
}


def test_promotion_hands_the_agent_rail_the_release_branch(
    client, make_token, monkeypatch
):
    dispatched: dict = {}
    release = _wire_promote(monkeypatch, dep=EXTERNAL_PROD, dispatched=dispatched)
    resp = client.post(
        f"/api/v1/releases/{release['id']}/promote", headers=_auth(make_token)
    )
    assert resp.status_code == 200
    assert resp.json()["mode"] == "agent"
    # the branch IS the pin: production ships the build UAT tested even after
    # main moved during testing
    assert dispatched["release_branch"] == "release/2026.07.25.1"
    assert dispatched["ref"] == HEAD_SHA


# --- US-90.1: a failed release retries; a rejected one is final -------------


def _wire_retry(monkeypatch, *, release, calls):
    """Stub the retry endpoint's collaborators, recording what each was
    handed — the pin assertions read `calls` afterwards."""
    from app.routers import releases as releases_router

    async def fake_get(settings, token, table, params):
        if table == "releases":
            return [release]
        return []

    monkeypatch.setattr(releases_router, "postgrest_get", fake_get)
    monkeypatch.setattr(
        releases_router.db,
        "count_release_attempts",
        lambda s, rid: {"prep": 2, "deploy": 1},
    )
    monkeypatch.setattr(
        releases_router.db,
        "dispatch_release_prep_for",
        lambda s, rid, org, requested_by=None: calls.update(
            {"prep": {"release_id": rid, "requested_by": requested_by}}
        )
        or {"run_id": "prep-2"},
    )
    monkeypatch.setattr(
        releases_router.db,
        "get_release_uat_deployment_id",
        lambda s, pid: "dep-uat",
    )
    monkeypatch.setattr(
        releases_router.db,
        "get_deployment_for_agent",
        lambda s, d, o: {
            "deployment": {"id": "dep-uat", "org_id": ORG_ID},
            "server": None,
            "project": {"repo_full_name": "acme/demo", "name": "Demo"},
        },
    )
    monkeypatch.setattr(
        releases_router.deploy,
        "launch_release_uat_deploy",
        lambda s, rel, dep, server, project, actor: calls.update(
            {"deploy": {"release": rel, "actor": actor}}
        )
        or "dr-retry",
    )
    monkeypatch.setattr(
        releases_router.db,
        "update_release",
        lambda s, rid, patch: calls.setdefault("patches", []).append(patch) or {},
    )


def _failed_release(**over):
    return {
        "id": str(uuid.uuid4()),
        "org_id": ORG_ID,
        "project_id": PROJECT_ID,
        "status": "failed",
        "version": "2026.08.13.1",
        "commit_sha": HEAD_SHA,
        "notes_written_at": None,
        "promoted_at": None,
        "released_at": None,
        "failure_reason": "the agent finished without calling submit_release_notes",
        **over,
    }


def test_retry_of_a_dead_prep_queues_a_fresh_notes_attempt(
    client, make_token, monkeypatch
):
    calls: dict = {}
    release = _failed_release()
    _wire_retry(monkeypatch, release=release, calls=calls)
    resp = client.post(
        f"/api/v1/releases/{release['id']}/retry",
        headers=_auth(make_token, sub="manager-1"),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["leg"] == "notes"
    assert body["attempt"] == 3  # two prior tries read as two prior tries
    # AC3/AC4: the same release re-queued, the clicker audited.
    assert calls["prep"]["release_id"] == release["id"]
    assert calls["prep"]["requested_by"] == "manager-1"
    # The stale reason clears; the failed attempt row keeps the history.
    assert {"failure_reason": None} in calls["patches"]
    assert "deploy" not in calls  # the completed-leg rule: nothing else re-ran


def test_retry_of_a_dead_deploy_refires_the_pinned_commit_without_an_agent(
    client, make_token, monkeypatch
):
    calls: dict = {}
    release = _failed_release(
        status="uat-deploy-failed",
        notes_written_at="2026-08-13T17:00:00Z",
        failure_reason="ssh: connect to host timed out",
    )
    _wire_retry(monkeypatch, release=release, calls=calls)
    resp = client.post(
        f"/api/v1/releases/{release['id']}/retry",
        # AC3: a body naming another commit changes nothing — the endpoint
        # takes no body, and the pin is read off the release row.
        json={"commit_sha": "e" * 40, "version": "9999.01.01.9"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["leg"] == "deploy"
    assert body["attempt"] == 2
    assert body["commit_sha"] == HEAD_SHA
    # The pin never moves: the deploy got the stored release row, sha intact.
    assert calls["deploy"]["release"]["commit_sha"] == HEAD_SHA
    assert calls["deploy"]["release"]["version"] == "2026.08.13.1"
    assert "prep" not in calls  # notes are written; that leg is not redone
    assert {
        "status": "deploying",
        "uat_deployment_run_id": "dr-retry",
        "failure_reason": None,
    } in calls["patches"]


def test_a_prep_failure_after_notes_landed_retries_the_deploy_leg(
    client, make_token, monkeypatch
):
    """Leg detection is the release's record, not its status label: notes
    already written means the remaining work is the deterministic deploy."""
    calls: dict = {}
    release = _failed_release(notes_written_at="2026-08-13T17:00:00Z")
    _wire_retry(monkeypatch, release=release, calls=calls)
    resp = client.post(
        f"/api/v1/releases/{release['id']}/retry", headers=_auth(make_token)
    )
    assert resp.status_code == 200
    assert resp.json()["leg"] == "deploy"
    assert "prep" not in calls


@pytest.mark.parametrize(
    "status", ["queued", "running", "uat-deployed", "rejected", "cancelled",
               "released", "rolled-back"]
)
def test_only_a_failed_release_can_retry(client, make_token, monkeypatch, status):
    calls: dict = {}
    release = _failed_release(status=status)
    _wire_retry(monkeypatch, release=release, calls=calls)
    resp = client.post(
        f"/api/v1/releases/{release['id']}/retry", headers=_auth(make_token)
    )
    assert resp.status_code == 409
    assert "only a failed release" in resp.json()["detail"]
    assert calls == {}  # nothing dispatched, nothing patched


def test_a_promoted_release_never_retries(client, make_token, monkeypatch):
    calls: dict = {}
    release = _failed_release(promoted_at="2026-08-13T18:00:00Z")
    _wire_retry(monkeypatch, release=release, calls=calls)
    resp = client.post(
        f"/api/v1/releases/{release['id']}/retry", headers=_auth(make_token)
    )
    assert resp.status_code == 409
    assert "rollback" in resp.json()["detail"]
    assert calls == {}


def test_retry_relays_a_dispatch_refusal(client, make_token, monkeypatch):
    """A prep already queued (double-click, or a parallel retry) answers 409
    with the dispatcher's own reason — never a second queued row."""
    calls: dict = {}
    release = _failed_release()
    _wire_retry(monkeypatch, release=release, calls=calls)
    from app.routers import releases as releases_router

    monkeypatch.setattr(
        releases_router.db,
        "dispatch_release_prep_for",
        lambda s, rid, org, requested_by=None: {
            "error": "release prep is already queued or running"
        },
    )
    resp = client.post(
        f"/api/v1/releases/{release['id']}/retry", headers=_auth(make_token)
    )
    assert resp.status_code == 409
    assert "already queued" in resp.json()["detail"]
    assert calls.get("patches") is None  # the failure reason was not cleared


def test_promotion_on_the_human_rail_pins_the_branch_too(
    client, make_token, monkeypatch
):
    dispatched: dict = {}
    release = _wire_promote(
        monkeypatch,
        dep={**EXTERNAL_PROD, "protected": True},
        dispatched=dispatched,
        refusal="protected deployments are human-only",
    )
    resp = client.post(
        f"/api/v1/releases/{release['id']}/promote", headers=_auth(make_token)
    )
    assert resp.status_code == 200
    assert resp.json()["mode"] == "direct"
    override = dispatched["ctx"]["override"]
    assert override["sha"] == HEAD_SHA
    assert override["branch"] == "release/2026.07.25.1"
    # a null server must not be dereferenced anywhere on this path
    assert dispatched["ctx"]["server"] is None
