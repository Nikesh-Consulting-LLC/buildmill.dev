"""US-81.2/81.3/81.4: the suite run endpoints — manual trigger, release
rerun, and the sign-off waiver. PostgREST, GitHub and the pipeline are
stubbed; these cover what the endpoints decide."""

import uuid

from app import suites as suites_pipeline
from app.routers import suite_runs as routes

ORG_ID = str(uuid.uuid4())
PROJECT_ID = str(uuid.uuid4())
SUITE_ID = str(uuid.uuid4())
DEPLOYMENT_ID = str(uuid.uuid4())
RELEASE_ID = str(uuid.uuid4())
SERVER_ID = str(uuid.uuid4())
HEAD_SHA = "a" * 40


def _auth(make_token, **claims):
    return {"Authorization": f"Bearer {make_token(**claims)}"}


def _suite(**over):
    return {
        "id": SUITE_ID,
        "org_id": ORG_ID,
        "project_id": PROJECT_ID,
        "name": "api",
        "layer": "api",
        "run_command": "python -m pytest",
        "results_path": "test-results/junit.xml",
        "server_id": None,
        "run_on_uat": True,
        "run_on_prod": False,
        "blocks_signoff": False,
        "timeout_minutes": 30,
        "status": "active",
        **over,
    }


def _deployment(**over):
    return {
        "id": DEPLOYMENT_ID,
        "org_id": ORG_ID,
        "project_id": PROJECT_ID,
        "server_id": SERVER_ID,
        "branch": "main",
        "website_url": "https://uat.example.dev",
        **over,
    }


def _release(**over):
    return {
        "id": RELEASE_ID,
        "org_id": ORG_ID,
        "project_id": PROJECT_ID,
        "version": "2026.08.11.1",
        "status": "uat-deployed",
        "commit_sha": HEAD_SHA,
        **over,
    }


def _wire(monkeypatch, *, tables=None, launched=None, active=False):
    tables = tables or {}

    async def fake_get(settings, token, table, params):
        return tables.get(table, [])

    async def fake_rpc(settings, token, fn, args):
        return None

    async def fake_token(settings, org_id, repo_full):
        return "gh-token"

    async def fake_branch(token, owner, repo, branch):
        return {"commit": {"sha": HEAD_SHA}}

    def fake_create(settings, **kw):
        if active:
            raise suites_pipeline.SuiteRunActive()
        if launched is not None:
            launched.append(kw)
        return "run-1"

    def fake_launch(settings, ctx):
        if launched is not None:
            launched.append({"launch_ctx": ctx})

    monkeypatch.setattr(routes, "postgrest_get", fake_get)
    monkeypatch.setattr(routes, "rpc", fake_rpc)
    monkeypatch.setattr(routes.github_tokens, "token_for_org", fake_token)
    monkeypatch.setattr(routes.github, "get_branch", fake_branch)
    monkeypatch.setattr(routes.suites_mod, "create_suite_run", fake_create)
    monkeypatch.setattr(routes.suites_mod, "launch", fake_launch)


PROJECT = {
    "id": PROJECT_ID,
    "org_id": ORG_ID,
    "repo_full_name": "acme/demo",
    "release_uat_deployment_id": DEPLOYMENT_ID,
    "release_prod_deployment_id": None,
}


class TestManualRun:
    def test_requires_auth(self, client):
        resp = client.post(
            f"/api/v1/suites/{SUITE_ID}/run", json={"deployment_id": DEPLOYMENT_ID}
        )
        assert resp.status_code == 401

    def test_runs_against_the_branch_head(self, client, make_token, monkeypatch):
        launched = []
        _wire(
            monkeypatch,
            tables={
                "test_suites": [_suite()],
                "deployments": [_deployment()],
                "projects": [PROJECT],
                "servers": [{"id": SERVER_ID, "org_id": ORG_ID, "host": "uat.box"}],
            },
            launched=launched,
        )
        resp = client.post(
            f"/api/v1/suites/{SUITE_ID}/run",
            json={"deployment_id": DEPLOYMENT_ID},
            headers=_auth(make_token),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["run_id"] == "run-1"
        assert body["commit_sha"] == HEAD_SHA
        create = launched[0]
        assert create["trigger"] == "manual"
        assert create["base_url"] == "https://uat.example.dev"
        ctx = launched[1]["launch_ctx"]
        assert ctx["release"] is None
        assert ctx["repo_full_name"] == "acme/demo"

    def test_cross_project_deployment_is_refused(self, client, make_token, monkeypatch):
        _wire(
            monkeypatch,
            tables={
                "test_suites": [_suite()],
                "deployments": [_deployment(project_id=str(uuid.uuid4()))],
                "projects": [PROJECT],
            },
        )
        resp = client.post(
            f"/api/v1/suites/{SUITE_ID}/run",
            json={"deployment_id": DEPLOYMENT_ID},
            headers=_auth(make_token),
        )
        assert resp.status_code == 400
        assert "different project" in resp.json()["detail"]

    def test_deployment_without_url_is_refused(self, client, make_token, monkeypatch):
        _wire(
            monkeypatch,
            tables={
                "test_suites": [_suite()],
                "deployments": [_deployment(website_url=None)],
                "projects": [PROJECT],
            },
        )
        resp = client.post(
            f"/api/v1/suites/{SUITE_ID}/run",
            json={"deployment_id": DEPLOYMENT_ID},
            headers=_auth(make_token),
        )
        assert resp.status_code == 400
        assert "website URL" in resp.json()["detail"]

    def test_single_flight_answers_409(self, client, make_token, monkeypatch):
        _wire(
            monkeypatch,
            tables={
                "test_suites": [_suite()],
                "deployments": [_deployment()],
                "projects": [PROJECT],
                "servers": [{"id": SERVER_ID, "org_id": ORG_ID}],
            },
            active=True,
        )
        resp = client.post(
            f"/api/v1/suites/{SUITE_ID}/run",
            json={"deployment_id": DEPLOYMENT_ID},
            headers=_auth(make_token),
        )
        assert resp.status_code == 409

    def test_unknown_suite_is_404(self, client, make_token, monkeypatch):
        _wire(monkeypatch, tables={})
        resp = client.post(
            f"/api/v1/suites/{SUITE_ID}/run",
            json={"deployment_id": DEPLOYMENT_ID},
            headers=_auth(make_token),
        )
        assert resp.status_code == 404


class TestRerun:
    def test_pins_the_release_commit(self, client, make_token, monkeypatch):
        launched = []
        _wire(
            monkeypatch,
            tables={
                "releases": [_release()],
                "test_suites": [_suite()],
                "projects": [PROJECT],
                "deployments": [_deployment()],
                "servers": [{"id": SERVER_ID, "org_id": ORG_ID}],
            },
            launched=launched,
        )
        resp = client.post(
            f"/api/v1/releases/{RELEASE_ID}/suites/{SUITE_ID}/rerun",
            headers=_auth(make_token),
        )
        assert resp.status_code == 200
        create = launched[0]
        assert create["commit_sha"] == HEAD_SHA
        assert create["trigger"] == "uat-deploy"
        assert create["release_id"] == RELEASE_ID

    def test_refused_off_uat(self, client, make_token, monkeypatch):
        _wire(
            monkeypatch,
            tables={
                "releases": [_release(status="uat-signed-off")],
                "test_suites": [_suite()],
            },
        )
        resp = client.post(
            f"/api/v1/releases/{RELEASE_ID}/suites/{SUITE_ID}/rerun",
            headers=_auth(make_token),
        )
        assert resp.status_code == 409
        assert "uat-signed-off" in resp.json()["detail"]


class TestWaive:
    def test_waives_the_latest_failed_run(self, client, make_token, monkeypatch):
        waived = {}

        def fake_waive(settings, *, run_id, waived_by, reason):
            waived.update(run_id=run_id, waived_by=waived_by, reason=reason)

        _wire(
            monkeypatch,
            tables={
                "releases": [_release()],
                "suite_runs": [{"id": "run-9", "status": "failed"}],
            },
        )
        monkeypatch.setattr(routes.suites_mod, "waive_run", fake_waive)
        resp = client.post(
            f"/api/v1/releases/{RELEASE_ID}/suites/{SUITE_ID}/waive",
            json={"reason": "flaky selector, tracked as BUG-9"},
            headers=_auth(make_token),
        )
        assert resp.status_code == 200
        assert waived["run_id"] == "run-9"
        assert "flaky selector" in waived["reason"]

    def test_succeeded_needs_no_waiver(self, client, make_token, monkeypatch):
        _wire(
            monkeypatch,
            tables={
                "releases": [_release()],
                "suite_runs": [{"id": "run-9", "status": "succeeded"}],
            },
        )
        resp = client.post(
            f"/api/v1/releases/{RELEASE_ID}/suites/{SUITE_ID}/waive",
            json={"reason": "not needed"},
            headers=_auth(make_token),
        )
        assert resp.status_code == 409

    def test_in_flight_run_cannot_be_waived(self, client, make_token, monkeypatch):
        _wire(
            monkeypatch,
            tables={
                "releases": [_release()],
                "suite_runs": [{"id": "run-9", "status": "running"}],
            },
        )
        resp = client.post(
            f"/api/v1/releases/{RELEASE_ID}/suites/{SUITE_ID}/waive",
            json={"reason": "too soon"},
            headers=_auth(make_token),
        )
        assert resp.status_code == 409

    def test_no_run_is_404(self, client, make_token, monkeypatch):
        _wire(monkeypatch, tables={"releases": [_release()], "suite_runs": []})
        resp = client.post(
            f"/api/v1/releases/{RELEASE_ID}/suites/{SUITE_ID}/waive",
            json={"reason": "nothing ran"},
            headers=_auth(make_token),
        )
        assert resp.status_code == 404
