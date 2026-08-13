"""External deployments (Phase 50): a deployment that ships by merging.

`kind = 'external'` means there is no machine. Deploying merges the source
branch into the branch somebody else's pipeline watches, and the run ends
there — success means the merge commit exists on the target branch, not that
anything was deployed.

Three groups of tests:

* the merge pipeline itself (us-50.2) — success, the nothing-to-merge no-op,
  a refusal that leaves the pull request standing, and reuse of that PR;
* the refusals (us-50.3) — every SSH-shaped endpoint rejects an external
  deployment by kind, *before* touching SSH, storage or GitHub, because the
  browser is not the only caller;
* the release path (us-50.4) — the cut creates `release/<version>`, a branch
  failure is reported rather than fatal, and promotion carries that branch so
  the pin survives.
"""

from __future__ import annotations

import asyncio

import pytest

from app import deploy
from app.config import Settings
from app.github import GitHubError


def _auth(make_token, **claims):
    return {"Authorization": f"Bearer {make_token(**claims)}"}


EXTERNAL_ROW = {
    "id": "dep-x",
    "org_id": "org-1",
    "project_id": "proj-1",
    "kind": "external",
    "server_id": None,
    "name": "production",
    "branch": "main",
    "target_branch": "prod",
    "target_folder": None,
    "script": "",
    "run_timeout_minutes": 30,
    "servers": None,
    "projects": {"repo_full_name": "acme/site", "name": "Site"},
}

FACTORY_ROW = {
    "id": "dep-1",
    "org_id": "org-1",
    "project_id": "proj-1",
    "kind": "factory",
    "server_id": "srv-1",
    "name": "staging",
    "branch": "main",
    "target_branch": "",
    "target_folder": "/var/www/app",
    "script": "echo hi",
    "run_timeout_minutes": 30,
    "strategy": "releases",
    "servers": {
        "id": "srv-1",
        "org_id": "org-1",
        "host": "h",
        "port": 22,
        "username": "root",
        "auth_method": "password",
        "host_key_fingerprint": "SHA256:abc",
    },
    "projects": {"repo_full_name": "acme/site", "name": "Site"},
}


def _external_visible(monkeypatch, **overrides):
    async def fake_get(settings, token, table, params):
        return [{**EXTERNAL_ROW, **overrides}]

    monkeypatch.setattr("app.routers.deployments.postgrest_get", fake_get)


# ---------------------------------------------------------------------------
# The merge pipeline (US-50.2)
# ---------------------------------------------------------------------------


class _Recorder:
    """Everything the pipeline writes, without a database."""

    def __init__(self):
        self.updates: dict[str, object] = {}
        self.events: list[tuple[str, str]] = []
        self.current_run: str | None = None
        self.notifications: list[tuple[str, str]] = []
        self.log = ""

    def install(self, monkeypatch):
        def update(settings, run_id, fields):
            self.updates.update(fields)
            if "log" in fields:
                self.log = fields["log"]

        monkeypatch.setattr(deploy, "_update_run", update)
        monkeypatch.setattr(
            deploy,
            "record_event",
            lambda settings, org, run, phase, message, data=None: self.events.append(
                (phase, message)
            ),
        )
        monkeypatch.setattr(
            deploy,
            "_set_current_run",
            lambda settings, dep_id, run_id: setattr(self, "current_run", run_id),
        )
        monkeypatch.setattr(
            deploy.notify,
            "notify_deployment_event",
            lambda settings, **kw: self.notifications.append(
                (kw["event"], kw["status"])
            ),
        )
        return self


@pytest.fixture()
def settings():
    return Settings(
        supabase_url="https://test.supabase.co",
        supabase_publishable_key="sb_publishable_test",
        cors_origins="http://localhost:3000",
        database_url="postgresql://test",
    )


def _github(monkeypatch, *, compare, pull=None, open_pull=None, merge=None):
    """Fake just the GitHub surface the merge pipeline touches."""
    calls: dict[str, object] = {"created_refs": [], "created_pulls": []}

    async def fake_token(settings, org_id, repo_full_name=None):
        return "gh-token"

    async def fake_branch(token, owner, repo, branch):
        return {"commit": {"sha": "src111222333", "commit": {"message": "a change"}}}

    async def fake_compare(token, owner, repo, base, head):
        calls["compare"] = (base, head)
        return compare

    async def fake_find(token, owner, repo, head, base):
        calls["find"] = (head, base)
        return open_pull

    async def fake_create_pull(token, owner, repo, head, base, title, body):
        calls["created_pulls"].append((head, base))
        return pull

    async def fake_merge(token, owner, repo, number, merge_method="squash"):
        calls["merge"] = (number, merge_method)
        if isinstance(merge, Exception):
            raise merge
        return merge

    async def fake_get_ref(token, owner, repo, name):
        return None

    async def fake_create_ref(token, owner, repo, name, sha):
        calls["created_refs"].append((name, sha))

    monkeypatch.setattr("app.github_tokens.token_for_org", fake_token)
    monkeypatch.setattr("app.github.get_branch", fake_branch)
    monkeypatch.setattr("app.github.compare_commits", fake_compare)
    monkeypatch.setattr("app.github.find_open_pull", fake_find)
    monkeypatch.setattr("app.github.create_pull", fake_create_pull)
    monkeypatch.setattr("app.github.merge_pull_request", fake_merge)
    monkeypatch.setattr("app.github.get_ref", fake_get_ref)
    monkeypatch.setattr("app.github.create_ref", fake_create_ref)
    return calls


def _ctx(**overrides):
    return {
        "run_id": "run-x1",
        "org_id": "org-1",
        "deployment": dict(EXTERNAL_ROW),
        "server": None,
        "repo_full_name": "acme/site",
        "project_name": "Site",
        "triggered_by": "manager@example.com",
        **overrides,
    }


AHEAD = {"status": "ahead", "ahead_by": 2, "commits": []}
PR = {"number": 7, "html_url": "https://github.com/acme/site/pull/7"}


def test_merge_run_succeeds_naming_both_commits(settings, monkeypatch):
    rec = _Recorder().install(monkeypatch)
    calls = _github(monkeypatch, compare=AHEAD, pull=PR, merge="merge999")

    asyncio.run(deploy.run_merge_pipeline(settings, _ctx()))

    assert rec.updates["status"] == "succeeded"
    # commit_sha stays the SOURCE commit: GET /issues/{id}/deployments tests it
    # for ancestry, and recording the merge commit there breaks that panel.
    assert rec.updates["commit_sha"] == "src111222333"
    assert rec.updates["merge_commit_sha"] == "merge999"
    assert rec.updates["pr_number"] == 7
    assert rec.current_run == "run-x1"
    assert calls["merge"] == (7, "merge")  # never squash, never rebase
    assert calls["compare"] == ("prod", "src111222333")
    assert "prod" in rec.log and "src1112" in rec.log and "merge99" in rec.log
    assert ("succeeded", "succeeded") in rec.notifications
    # The timeline mirrors the factory pipeline's stages, so the run panel
    # needs no new shape: resolve -> pull request -> merge -> done.
    phases = [p for p, _ in rec.events]
    assert [p for i, p in enumerate(phases) if i == 0 or phases[i - 1] != p] == [
        "fetch",
        "transfer",
        "release",
        "done",
    ]


def test_nothing_to_merge_is_a_success(settings, monkeypatch):
    """Re-deploying the current build is the ordinary case; a red run for
    'already live' would train the manager to ignore red."""
    rec = _Recorder().install(monkeypatch)
    calls = _github(monkeypatch, compare={"status": "identical"})

    asyncio.run(deploy.run_merge_pipeline(settings, _ctx()))

    assert rec.updates["status"] == "succeeded"
    assert "nothing to merge" in rec.log.lower()
    assert rec.updates.get("pr_number") is None
    assert calls["created_pulls"] == []  # no PR opened


def test_target_already_contains_the_commit_is_also_a_no_op(settings, monkeypatch):
    rec = _Recorder().install(monkeypatch)
    _github(monkeypatch, compare={"status": "behind", "behind_by": 3})
    asyncio.run(deploy.run_merge_pipeline(settings, _ctx()))
    assert rec.updates["status"] == "succeeded"
    assert "nothing to merge" in rec.log.lower()


def test_a_refused_merge_fails_the_run_and_leaves_the_pr(settings, monkeypatch):
    rec = _Recorder().install(monkeypatch)
    _github(
        monkeypatch,
        compare=AHEAD,
        pull=PR,
        merge=GitHubError("GitHub merge failed: At least 1 approving review is required"),
    )

    asyncio.run(deploy.run_merge_pipeline(settings, _ctx()))

    assert rec.updates["status"] == "failed"
    assert "approving review" in rec.log
    assert PR["html_url"] in rec.log
    # the PR number is recorded even though the merge did not happen — the run
    # that follows finds it rather than opening a second one
    assert rec.updates["pr_number"] == 7
    assert any(PR["html_url"] in msg for _, msg in rec.events)


def test_rerunning_after_a_refusal_reuses_the_open_pr(settings, monkeypatch):
    rec = _Recorder().install(monkeypatch)
    calls = _github(monkeypatch, compare=AHEAD, open_pull=PR, merge="merge999")

    asyncio.run(deploy.run_merge_pipeline(settings, _ctx()))

    assert calls["created_pulls"] == []  # reused, not duplicated
    assert calls["find"] == ("main", "prod")
    assert rec.updates["status"] == "succeeded"
    assert "reusing open pull request #7" in rec.log


def test_a_pinned_commit_gets_a_branch_to_open_the_pr_from(settings, monkeypatch):
    """US-50.4's fallback: a release cut before the branch existed still
    deploys — the merge takes the branch when it exists and materializes one
    at the commit when it does not."""
    rec = _Recorder().install(monkeypatch)
    calls = _github(monkeypatch, compare=AHEAD, pull=PR, merge="merge999")

    asyncio.run(
        deploy.run_merge_pipeline(
            settings,
            _ctx(
                override={
                    "ref": "2026.07.29.1",
                    "sha": "pinned0000ff",
                    "message": "Release 2026.07.29.1",
                    "branch": "release/2026.07.29.1",
                }
            ),
        )
    )

    assert calls["created_refs"] == [("release/2026.07.29.1", "pinned0000ff")]
    assert calls["created_pulls"] == [("release/2026.07.29.1", "prod")]
    assert rec.updates["commit_sha"] == "pinned0000ff"
    assert rec.updates["status"] == "succeeded"


def test_a_pin_with_no_release_branch_still_merges(settings, monkeypatch):
    rec = _Recorder().install(monkeypatch)
    calls = _github(monkeypatch, compare=AHEAD, pull=PR, merge="merge999")

    asyncio.run(
        deploy.run_merge_pipeline(
            settings,
            _ctx(override={"ref": "abc", "sha": "pinned0000ff", "message": ""}),
        )
    )

    assert calls["created_refs"] == [("buildmill-deploy/pinned0000ff", "pinned0000ff")]
    assert rec.updates["status"] == "succeeded"


def test_a_missing_target_branch_fails_before_github(settings, monkeypatch):
    rec = _Recorder().install(monkeypatch)
    _github(monkeypatch, compare=AHEAD)
    dep = {**EXTERNAL_ROW, "target_branch": ""}
    asyncio.run(deploy.run_merge_pipeline(settings, _ctx(deployment=dep)))
    assert rec.updates["status"] == "failed"
    assert "target branch" in rec.log


def test_launch_picks_the_pipeline_from_the_kind(settings, monkeypatch):
    """`deploy.launch` is the one place that learns a deployment's kind —
    every caller hands over the same ctx either way."""
    picked = []

    async def fake_merge(settings, ctx):
        picked.append("merge")

    async def fake_ssh(settings, ctx):
        picked.append("ssh")

    monkeypatch.setattr(deploy, "run_merge_pipeline", fake_merge)
    monkeypatch.setattr(deploy, "run_pipeline", fake_ssh)

    async def drive(dep):
        deploy.launch(settings, _ctx(deployment=dep))
        await asyncio.sleep(0)

    asyncio.run(drive(dict(EXTERNAL_ROW)))
    asyncio.run(drive(dict(FACTORY_ROW)))
    asyncio.run(drive({"id": "d", "name": "n", "project_id": "p"}))  # no kind column
    assert picked == ["merge", "ssh", "ssh"]


# ---------------------------------------------------------------------------
# Running one (US-50.2), and the single-flight/protection rails it keeps
# ---------------------------------------------------------------------------


def test_run_external_launches_without_a_server(client, make_token, monkeypatch):
    launched = {}
    _external_visible(monkeypatch)
    monkeypatch.setattr(
        "app.deploy.create_run",
        lambda settings, dep, by, email, source="branch", zip_filename=None,
        branch_override=None: "run-x1",
    )
    monkeypatch.setattr("app.deploy.launch", lambda settings, ctx: launched.update(ctx))

    resp = client.post("/api/v1/deployments/dep-x/run", headers=_auth(make_token))
    assert resp.status_code == 202
    assert launched["server"] is None
    assert launched["deployment"]["kind"] == "external"


def test_run_external_without_a_target_branch_is_400(client, make_token, monkeypatch):
    _external_visible(monkeypatch, target_branch="")
    resp = client.post("/api/v1/deployments/dep-x/run", headers=_auth(make_token))
    assert resp.status_code == 400
    assert "target branch" in resp.json()["detail"]


def test_external_single_flight_still_holds(client, make_token, monkeypatch):
    from app import deploy as deploy_mod

    _external_visible(monkeypatch)

    def busy(settings, dep, by, email, source="branch", zip_filename=None,
             branch_override=None):
        raise deploy_mod.RunActive()

    monkeypatch.setattr("app.deploy.create_run", busy)
    resp = client.post("/api/v1/deployments/dep-x/run", headers=_auth(make_token))
    assert resp.status_code == 409


def test_protected_external_refuses_a_ref_override(client, make_token, monkeypatch):
    _external_visible(monkeypatch, protected=True)

    async def fake_rpc(settings, token, fn, args):
        return True  # even an owner cannot override on a protected deployment

    monkeypatch.setattr("app.routers.deployments.rpc", fake_rpc)
    resp = client.post(
        "/api/v1/deployments/dep-x/run",
        json={"ref": "feat-x"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 403


def test_protected_external_is_owners_only(client, make_token, monkeypatch):
    _external_visible(monkeypatch, protected=True)

    async def fake_rpc(settings, token, fn, args):
        return False

    monkeypatch.setattr("app.routers.deployments.rpc", fake_rpc)
    resp = client.post("/api/v1/deployments/dep-x/run", headers=_auth(make_token))
    assert resp.status_code == 403
    assert "owners only" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# The refusals (US-50.3)
# ---------------------------------------------------------------------------
#
# Hidden is a courtesy; refused is the guarantee. Each of these must answer
# 400 with a reason — and must do so before reaching SSH, storage or GitHub,
# which is what the un-mocked downstream in these tests proves.

REFUSED = [
    ("post", "/api/v1/deployments/dep-x/zip",
     {"files": {"file": ("build.zip", b"PK\x03\x04", "application/zip")}}),
    ("post", "/api/v1/deployments/dep-x/redeploy-zip", {}),
    ("post", "/api/v1/deployments/dep-x/runs/run-1/redeploy", {}),
    ("post", "/api/v1/deployments/dep-x/runs/run-1/promote",
     {"json": {"target_deployment_id": "dep-2"}}),
    ("post", "/api/v1/deployments/dep-x/rollback", {"json": {"run_id": "run-1"}}),
    ("post", "/api/v1/deployments/dep-x/preflight", {}),
    ("post", "/api/v1/deployments/dep-x/health-check", {}),
    ("put", "/api/v1/deployments/dep-x/env/TOKEN", {"json": {"value": "s3cret"}}),
    ("delete", "/api/v1/deployments/dep-x/env/TOKEN", {}),
]


@pytest.mark.parametrize("op", REFUSED, ids=lambda op: f"{op[0]} {op[1]}")
def test_ssh_shaped_endpoints_refuse_an_external_deployment(
    client, make_token, monkeypatch, op
):
    _external_visible(monkeypatch)
    method, url, kwargs = op
    resp = getattr(client, method)(url, headers=_auth(make_token), **kwargs)
    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert "external deployment" in detail
    assert "prod" in detail  # names where it does merge instead


def test_an_external_deployment_is_not_a_promotion_target(
    client, make_token, monkeypatch
):
    """The TARGET's kind applies too — a promotion ships a payload, and an
    external deployment has nowhere to put one."""

    async def fake_get(settings, token, table, params):
        if params["id"] == "eq.dep-1":
            return [FACTORY_ROW]
        return [{**EXTERNAL_ROW, "id": "dep-x", "project_id": "proj-1"}]

    monkeypatch.setattr("app.routers.deployments.postgrest_get", fake_get)
    monkeypatch.setattr(
        "app.deploy.get_run",
        lambda settings, run_id: {
            "id": run_id,
            "deployment_id": "dep-1",
            "status": "succeeded",
            "source": "branch",
            "commit_sha": "abc",
            "artifact_path": "p",
        },
    )
    resp = client.post(
        "/api/v1/deployments/dep-1/runs/run-1/promote",
        json={"target_deployment_id": "dep-x"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 400
    assert "external deployment" in resp.json()["detail"]


def test_factory_endpoints_are_untouched(client, make_token, monkeypatch):
    """The guard must be by kind and nothing else — a factory deployment's
    rollback still fails for its own, older reason."""

    async def fake_get(settings, token, table, params):
        return [{**FACTORY_ROW, "strategy": "in-place"}]

    monkeypatch.setattr("app.routers.deployments.postgrest_get", fake_get)
    resp = client.post(
        "/api/v1/deployments/dep-1/rollback",
        json={"run_id": "run-9"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 400
    assert "in-place" in resp.json()["detail"]


# --- drift changes meaning rather than disappearing --------------------------


def _drift_github(monkeypatch, compare):
    seen = {}

    async def fake_get(settings, token, table, params):
        if table == "deployments":
            return [EXTERNAL_ROW]
        return [{"id": "c1", "method": "app", "installation_id": 42,
                 "vault_secret_id": None, "repos": []}]

    async def fake_mint(settings, installation_id):
        return "inst-token"

    async def fake_compare(token, owner, repo, base, head):
        seen["args"] = (base, head)
        return compare

    monkeypatch.setattr("app.routers.deployments.postgrest_get", fake_get)
    monkeypatch.setattr("app.github_tokens.postgrest_get", fake_get)
    monkeypatch.setattr("app.github_tokens.github.mint_installation_token", fake_mint)
    monkeypatch.setattr("app.github.compare_commits", fake_compare)
    return seen


def test_external_drift_compares_target_with_source(client, make_token, monkeypatch):
    seen = _drift_github(
        monkeypatch,
        {
            "status": "ahead",
            "ahead_by": 1,
            "commits": [
                {"sha": "c1", "commit": {"message": "a fix",
                                          "author": {"name": "a", "date": "d"}}}
            ],
        },
    )
    monkeypatch.setattr(
        "app.deploy.map_commits_to_issues", lambda settings, project, shas: {}
    )
    resp = client.get("/api/v1/deployments/dep-x/drift", headers=_auth(make_token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "behind"
    assert body["behind_by"] == 1
    # target...source, and it needed no run history to answer
    assert seen["args"] == ("prod", "main")


def test_external_drift_answers_before_any_run(client, make_token, monkeypatch):
    _drift_github(monkeypatch, {"status": "identical"})
    resp = client.get("/api/v1/deployments/dep-x/drift", headers=_auth(make_token))
    assert resp.json() == {"state": "up-to-date"}


# --- duplicate keeps the kind (US-50.1) --------------------------------------


def test_duplicate_makes_a_sibling_of_the_same_kind(client, make_token, monkeypatch):
    posted = {}

    async def fake_post(settings, token, table, body):
        posted.update(body)
        return [{**body, "id": "dep-x2"}]

    _external_visible(monkeypatch)
    monkeypatch.setattr("app.supabase.postgrest_post", fake_post)
    monkeypatch.setattr("app.deploy.list_env_var_names", lambda settings, dep_id: [])

    resp = client.post(
        "/api/v1/deployments/dep-x/duplicate",
        json={"name": "production-2"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 201
    assert posted["kind"] == "external"
    assert posted["target_branch"] == "prod"
    assert posted["server_id"] is None
