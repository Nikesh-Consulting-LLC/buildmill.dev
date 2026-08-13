"""Deployment definitions (US-1.31): the branch-list endpoint backing the
deployment form and the server-delete guard naming blocking deployments.

Deployment CRUD itself goes straight through the Supabase SDK under RLS —
there is deliberately no /api/v1/deployments CRUD surface to test here.
"""

import io
import tarfile

import httpx
import pytest


def _auth(make_token, **claims):
    return {"Authorization": f"Bearer {make_token(**claims)}"}


# --- branch list (deployment form) ------------------------------------------


def test_repo_branches_requires_auth(client):
    resp = client.get("/api/v1/github/repos/acme/site/branches")
    assert resp.status_code == 401


def test_repo_branches_returns_names_and_shas(client, make_token, monkeypatch):
    async def fake_get(settings, token, table, params):
        assert table == "github_connections"
        return [{"id": "c1", "method": "app", "installation_id": 42,
                  "vault_secret_id": None, "repos": []}]

    async def fake_mint(settings, installation_id):
        assert installation_id == 42
        return "inst-token"

    async def fake_branches(token, owner, repo):
        assert (token, owner, repo) == ("inst-token", "acme", "site")
        return [
            {"name": "main", "commit": {"sha": "abc123"}},
            {"name": "develop", "commit": {"sha": "def456"}},
        ]

    monkeypatch.setattr("app.github_tokens.postgrest_get", fake_get)
    monkeypatch.setattr("app.github_tokens.github.mint_installation_token", fake_mint)
    monkeypatch.setattr("app.github.list_branches", fake_branches)

    resp = client.get(
        "/api/v1/github/repos/acme/site/branches", headers=_auth(make_token)
    )
    assert resp.status_code == 200
    assert resp.json() == [
        {"name": "main", "commit_sha": "abc123"},
        {"name": "develop", "commit_sha": "def456"},
    ]


def test_repo_branches_no_installation_is_404(
    client, make_token, monkeypatch, settings_override
):
    settings_override.github_token = ""  # hermetic: no env fallback either

    async def fake_get(settings, token, table, params):
        return []

    monkeypatch.setattr("app.github_tokens.postgrest_get", fake_get)

    resp = client.get(
        "/api/v1/github/repos/acme/site/branches", headers=_auth(make_token)
    )
    assert resp.status_code == 404


def test_repo_branches_github_error_is_502(client, make_token, monkeypatch):
    async def fake_get(settings, token, table, params):
        return [{"id": "c1", "method": "app", "installation_id": 42,
                  "vault_secret_id": None, "repos": []}]

    async def fake_mint(settings, installation_id):
        return "inst-token"

    async def fake_branches(token, owner, repo):
        from app.github import GitHubError

        raise GitHubError("could not list branches (403)")

    monkeypatch.setattr("app.github_tokens.postgrest_get", fake_get)
    monkeypatch.setattr("app.github_tokens.github.mint_installation_token", fake_mint)
    monkeypatch.setattr("app.github.list_branches", fake_branches)

    resp = client.get(
        "/api/v1/github/repos/acme/site/branches", headers=_auth(make_token)
    )
    assert resp.status_code == 502


# --- server delete blocked while deployments reference it -------------------


def _restrict_error() -> httpx.HTTPStatusError:
    request = httpx.Request("DELETE", "http://test/rest/v1/servers")
    response = httpx.Response(409, request=request, json={"code": "23503"})
    return httpx.HTTPStatusError("conflict", request=request, response=response)


def test_delete_server_blocked_names_deployments(client, make_token, monkeypatch):
    async def fake_get(settings, token, table, params):
        if table == "servers":
            return [{"id": "srv-1", "org_id": "org-1", "name": "web-1"}]
        assert table == "deployments"
        assert params["server_id"] == "eq.srv-1"
        return [{"name": "production"}, {"name": "staging"}]

    async def fake_delete(settings, token, table, params):
        raise _restrict_error()

    monkeypatch.setattr("app.routers.servers.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.servers.postgrest_delete", fake_delete)

    resp = client.delete("/api/v1/servers/srv-1", headers=_auth(make_token))
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert "production" in detail and "staging" in detail


# --- run trigger (US-1.32) ---------------------------------------------------


DEPLOYMENT_ROW = {
    "id": "dep-1",
    "org_id": "org-1",
    "project_id": "proj-1",
    "server_id": "srv-1",
    "name": "staging",
    "branch": "main",
    "target_folder": "/var/www/app",
    "script": "echo hi",
    "run_timeout_minutes": 30,
    "servers": {
        "id": "srv-1",
        "org_id": "org-1",
        "host": "h",
        "port": 22,
        "username": "root",
        "auth_method": "password",
        "host_key_fingerprint": "SHA256:abc",
    },
    "projects": {"repo_full_name": "acme/site"},
}


def test_run_requires_auth(client):
    resp = client.post("/api/v1/deployments/dep-1/run")
    assert resp.status_code == 401


def test_run_foreign_deployment_is_404(client, make_token, monkeypatch):
    async def fake_get(settings, token, table, params):
        assert table == "deployments"
        return []  # RLS hides other orgs' rows

    monkeypatch.setattr("app.routers.deployments.postgrest_get", fake_get)
    resp = client.post("/api/v1/deployments/other-org/run", headers=_auth(make_token))
    assert resp.status_code == 404


def test_run_starts_pipeline(client, make_token, monkeypatch):
    launched = {}

    async def fake_get(settings, token, table, params):
        return [DEPLOYMENT_ROW]

    def fake_create_run(settings, deployment, started_by, started_by_email,
                        source="branch", zip_filename=None, branch_override=None):
        assert deployment["id"] == "dep-1"
        return "run-1"

    def fake_launch(settings, ctx):
        launched.update(ctx)

    monkeypatch.setattr("app.routers.deployments.postgrest_get", fake_get)
    monkeypatch.setattr("app.deploy.create_run", fake_create_run)
    monkeypatch.setattr("app.deploy.launch", fake_launch)

    resp = client.post("/api/v1/deployments/dep-1/run", headers=_auth(make_token))
    assert resp.status_code == 202
    assert resp.json() == {"run_id": "run-1", "status": "queued"}
    assert launched["run_id"] == "run-1"
    assert launched["repo_full_name"] == "acme/site"
    assert launched["server"]["host"] == "h"


def test_run_single_flight_conflict_is_409(client, make_token, monkeypatch):
    from app import deploy

    async def fake_get(settings, token, table, params):
        return [DEPLOYMENT_ROW]

    def fake_create_run(settings, deployment, started_by, started_by_email,
                        source="branch", zip_filename=None, branch_override=None):
        raise deploy.RunActive()

    monkeypatch.setattr("app.routers.deployments.postgrest_get", fake_get)
    monkeypatch.setattr("app.deploy.create_run", fake_create_run)

    resp = client.post("/api/v1/deployments/dep-1/run", headers=_auth(make_token))
    assert resp.status_code == 409
    assert "already active" in resp.json()["detail"]


# --- branch payload filtering (US-1.36) --------------------------------------


def _make_tarball(path, files):
    """files: dict of member-path (under a GitHub-style wrapper) -> bytes"""
    with tarfile.open(path, "w:gz") as tf:
        for name, content in files.items():
            info = tarfile.TarInfo(name=f"acme-site-abc123/{name}")
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))


def test_filter_tarball_source_folder_and_excludes(tmp_path):
    from app.deploy import filter_tarball

    src = str(tmp_path / "src.tgz")
    dest = str(tmp_path / "dest.tgz")
    _make_tarball(
        src,
        {
            "README.md": b"root readme",
            "apps/web/index.js": b"code",
            "apps/web/notes.md": b"docs",
            "apps/web/tests/spec.js": b"test",
            "apps/api/main.py": b"api",
        },
    )
    kept = filter_tarball(src, dest, "apps/web", "*.md\ntests/\n")
    assert kept == 1
    with tarfile.open(dest, "r:gz") as tf:
        names = [m.name for m in tf.getmembers()]
    assert names == ["payload/index.js"]


def test_filter_tarball_no_filters_keeps_everything(tmp_path):
    from app.deploy import filter_tarball

    src = str(tmp_path / "src.tgz")
    dest = str(tmp_path / "dest.tgz")
    _make_tarball(src, {"a.txt": b"1", "b/c.txt": b"2"})
    kept = filter_tarball(src, dest, "", "")
    assert kept == 2
    with tarfile.open(dest, "r:gz") as tf:
        names = sorted(m.name for m in tf.getmembers())
    assert names == ["payload/a.txt", "payload/b/c.txt"]


def test_filter_tarball_missing_source_folder_keeps_nothing(tmp_path):
    from app.deploy import filter_tarball

    src = str(tmp_path / "src.tgz")
    dest = str(tmp_path / "dest.tgz")
    _make_tarball(src, {"a.txt": b"1"})
    assert filter_tarball(src, dest, "does/not/exist", "") == 0


def test_matches_exclude_patterns():
    from app.deploy import matches_exclude

    assert matches_exclude("README.md", ["*.md"])
    assert matches_exclude("docs/guide.md", ["*.md"])
    assert matches_exclude("tests/spec.js", ["tests/"])
    assert matches_exclude("src/tests/spec.js", ["tests/"])
    assert not matches_exclude("src/index.js", ["*.md", "tests/"])
    assert matches_exclude(".env.example", [".env.example"])


# --- zip deploys (US-1.33) ----------------------------------------------------


def _zip_mocks(monkeypatch, active=False):
    staged = {}

    async def fake_put(settings, path, content, content_type="application/octet-stream"):
        staged["path"] = path
        staged["bytes"] = content

    monkeypatch.setattr("app.routers.deployments.storage.put_object", fake_put)
    monkeypatch.setattr(
        "app.deploy.has_active_run", lambda settings, dep_id: active
    )
    monkeypatch.setattr(
        "app.deploy.update_staged_zip",
        lambda settings, dep_id, filename, size, sha, email: staged.update(
            {"meta": (filename, size, email)}
        ),
    )
    monkeypatch.setattr(
        "app.deploy.create_run",
        lambda settings, dep, by, email, source="branch", zip_filename=None: "run-z1",
    )
    monkeypatch.setattr("app.deploy.launch", lambda settings, ctx: staged.update({"ctx": ctx}))
    return staged


def test_zip_upload_rejects_non_zip(client, make_token, monkeypatch):
    _dep_visible(monkeypatch)
    _zip_mocks(monkeypatch)
    resp = client.post(
        "/api/v1/deployments/dep-1/zip",
        files={"file": ("notzip.txt", b"hello", "text/plain")},
        headers=_auth(make_token),
    )
    assert resp.status_code == 400


def test_zip_upload_stages_and_starts_run(client, make_token, monkeypatch):
    _dep_visible(monkeypatch)
    staged = _zip_mocks(monkeypatch)
    resp = client.post(
        "/api/v1/deployments/dep-1/zip",
        files={"file": ("build.zip", b"PK\x03\x04fakezipdata", "application/zip")},
        headers=_auth(make_token),
    )
    assert resp.status_code == 202
    assert resp.json()["run_id"] == "run-z1"
    assert staged["path"] == "org-1/deployments/dep-1/staged.zip"
    assert staged["ctx"]["source"] == "zip"
    assert staged["ctx"]["zip_filename"] == "build.zip"


def test_zip_upload_blocked_while_run_active(client, make_token, monkeypatch):
    _dep_visible(monkeypatch)
    _zip_mocks(monkeypatch, active=True)
    resp = client.post(
        "/api/v1/deployments/dep-1/zip",
        files={"file": ("build.zip", b"PK\x03\x04data", "application/zip")},
        headers=_auth(make_token),
    )
    assert resp.status_code == 409


def test_redeploy_last_zip_requires_staged(client, make_token, monkeypatch):
    _dep_visible(monkeypatch)  # DEPLOYMENT_ROW has no staged_zip_filename
    resp = client.post(
        "/api/v1/deployments/dep-1/redeploy-zip", headers=_auth(make_token)
    )
    assert resp.status_code == 409


def test_redeploy_last_zip_starts_run(client, make_token, monkeypatch):
    launched = {}

    async def fake_get(settings, token, table, params):
        return [{**DEPLOYMENT_ROW, "staged_zip_filename": "build.zip"}]

    monkeypatch.setattr("app.routers.deployments.postgrest_get", fake_get)
    monkeypatch.setattr(
        "app.deploy.create_run",
        lambda settings, dep, by, email, source="branch", zip_filename=None: "run-z2",
    )
    monkeypatch.setattr("app.deploy.launch", lambda settings, ctx: launched.update(ctx))

    resp = client.post(
        "/api/v1/deployments/dep-1/redeploy-zip", headers=_auth(make_token)
    )
    assert resp.status_code == 202
    assert launched["zip_filename"] == "build.zip"


# --- rollback (US-1.39) ------------------------------------------------------


RELEASES_DEPLOYMENT = {**DEPLOYMENT_ROW, "strategy": "releases", "keep_releases": 5}

SUCCEEDED_RUN = {
    "id": "run-9",
    "deployment_id": "dep-1",
    "org_id": "org-1",
    "status": "succeeded",
    "source": "branch",
    "branch": "main",
    "commit_sha": "abc1234def",
    "release_path": "/var/www/app/releases/20260714-run9",
    "kind": "deploy",
}


def _releases_dep_visible(monkeypatch):
    async def fake_get(settings, token, table, params):
        return [RELEASES_DEPLOYMENT]

    monkeypatch.setattr("app.routers.deployments.postgrest_get", fake_get)


def test_rollback_requires_releases_strategy(client, make_token, monkeypatch):
    async def fake_get(settings, token, table, params):
        return [{**DEPLOYMENT_ROW, "strategy": "in-place"}]

    monkeypatch.setattr("app.routers.deployments.postgrest_get", fake_get)
    resp = client.post(
        "/api/v1/deployments/dep-1/rollback",
        json={"run_id": "run-9"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 400
    assert "in-place" in resp.json()["detail"]


def test_rollback_pruned_release_is_409(client, make_token, monkeypatch):
    _releases_dep_visible(monkeypatch)
    monkeypatch.setattr(
        "app.deploy.get_run",
        lambda settings, run_id: {**SUCCEEDED_RUN, "release_path": None},
    )
    resp = client.post(
        "/api/v1/deployments/dep-1/rollback",
        json={"run_id": "run-9"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 409
    assert "pruned" in resp.json()["detail"]


def test_rollback_starts_flip(client, make_token, monkeypatch):
    launched = {}
    _releases_dep_visible(monkeypatch)
    monkeypatch.setattr("app.deploy.get_run", lambda settings, run_id: dict(SUCCEEDED_RUN))
    monkeypatch.setattr(
        "app.deploy.create_rollback_run",
        lambda settings, dep, to_run, by, email: "run-10",
    )
    monkeypatch.setattr(
        "app.deploy.launch_rollback", lambda settings, ctx: launched.update(ctx)
    )

    resp = client.post(
        "/api/v1/deployments/dep-1/rollback",
        json={"run_id": "run-9"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 202
    assert resp.json()["run_id"] == "run-10"
    assert launched["to_run"]["release_path"] == SUCCEEDED_RUN["release_path"]


def test_rollback_wrong_deployment_is_404(client, make_token, monkeypatch):
    _releases_dep_visible(monkeypatch)
    monkeypatch.setattr(
        "app.deploy.get_run",
        lambda settings, run_id: {**SUCCEEDED_RUN, "deployment_id": "dep-OTHER"},
    )
    resp = client.post(
        "/api/v1/deployments/dep-1/rollback",
        json={"run_id": "run-9"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 404


# --- duplicate (US-1.42) -------------------------------------------------------


def test_duplicate_copies_config_and_env_values(client, make_token, monkeypatch):
    copied = {"objects": [], "rows": []}

    async def fake_post(settings, token, table, body):
        assert table == "deployments"
        assert body["name"] == "production"
        assert "protected" not in body  # the flag never copies
        assert body["script"] == DEPLOYMENT_ROW["script"]
        return [{**body, "id": "dep-2"}]

    async def fake_get_object(settings, path):
        return b"secret-value"

    async def fake_put_object(settings, path, content, content_type="application/octet-stream"):
        copied["objects"].append(path)

    _dep_visible(monkeypatch)
    monkeypatch.setattr("app.supabase.postgrest_post", fake_post)
    monkeypatch.setattr(
        "app.deploy.list_env_var_names", lambda settings, dep_id: ["DB_URL", "API_KEY"]
    )
    monkeypatch.setattr(
        "app.deploy.upsert_env_var",
        lambda settings, org, dep, name, actor="api": copied["rows"].append((dep, name)),
    )
    monkeypatch.setattr("app.routers.deployments.storage.get_object", fake_get_object)
    monkeypatch.setattr("app.routers.deployments.storage.put_object", fake_put_object)

    resp = client.post(
        "/api/v1/deployments/dep-1/duplicate",
        json={"name": "production"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] == "dep-2"
    assert body["copied_env_vars"] == 2
    assert "org-1/deployments/dep-2/env/DB_URL" in copied["objects"]
    assert ("dep-2", "API_KEY") in copied["rows"]
    # values never appear in the response
    assert "secret-value" not in resp.text


# --- ref override (US-1.50) ----------------------------------------------------


def test_override_rejected_on_protected(client, make_token, monkeypatch):
    async def fake_get(settings, token, table, params):
        return [{**DEPLOYMENT_ROW, "protected": True}]

    async def fake_rpc(settings, token, fn, args):
        return True  # even owners can't override on protected

    monkeypatch.setattr("app.routers.deployments.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.deployments.rpc", fake_rpc)

    resp = client.post(
        "/api/v1/deployments/dep-1/run",
        json={"ref": "feat-x"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 403
    assert "override" in resp.json()["detail"].lower()


def test_override_resolves_ref_and_launches(client, make_token, monkeypatch):
    launched = {}
    created = {}

    async def fake_get(settings, token, table, params):
        if table == "deployments":
            return [DEPLOYMENT_ROW]
        return [{"id": "c1", "method": "app", "installation_id": 42,
                  "vault_secret_id": None, "repos": []}]

    async def fake_mint(settings, installation_id):
        return "tok"

    async def fake_commit(token, owner, repo, ref):
        assert ref == "feat-x"
        return {"sha": "beefcafe123", "commit": {"message": "wip feature"}}

    def fake_create_run(settings, dep, by, email, source="branch",
                        zip_filename=None, branch_override=None):
        created["override"] = branch_override
        return "run-o1"

    monkeypatch.setattr("app.routers.deployments.postgrest_get", fake_get)
    monkeypatch.setattr("app.github_tokens.postgrest_get", fake_get)
    monkeypatch.setattr("app.github_tokens.github.mint_installation_token", fake_mint)
    monkeypatch.setattr("app.github.get_commit", fake_commit)
    monkeypatch.setattr("app.deploy.create_run", fake_create_run)
    monkeypatch.setattr("app.deploy.launch", lambda settings, ctx: launched.update(ctx))

    resp = client.post(
        "/api/v1/deployments/dep-1/run",
        json={"ref": "feat-x"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 202
    assert created["override"] == "feat-x"
    assert launched["override"]["sha"] == "beefcafe123"


def test_override_unresolvable_ref_is_400(client, make_token, monkeypatch):
    from app.github import GitHubError

    async def fake_get(settings, token, table, params):
        if table == "deployments":
            return [DEPLOYMENT_ROW]
        return [{"id": "c1", "method": "app", "installation_id": 42,
                  "vault_secret_id": None, "repos": []}]

    async def fake_mint(settings, installation_id):
        return "tok"

    async def fake_commit(token, owner, repo, ref):
        raise GitHubError("ref 'nope' not found in this repo")

    monkeypatch.setattr("app.routers.deployments.postgrest_get", fake_get)
    monkeypatch.setattr("app.github_tokens.postgrest_get", fake_get)
    monkeypatch.setattr("app.github_tokens.github.mint_installation_token", fake_mint)
    monkeypatch.setattr("app.github.get_commit", fake_commit)

    resp = client.post(
        "/api/v1/deployments/dep-1/run",
        json={"ref": "nope"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 400


# --- archive & redeploy (US-1.47) ----------------------------------------------


ARCHIVED_RUN = {
    "id": "run-a1",
    "deployment_id": "dep-1",
    "org_id": "org-1",
    "status": "succeeded",
    "source": "branch",
    "kind": "deploy",
    "branch": "main",
    "commit_sha": "abc1234def",
    "commit_message": "fix things",
    "zip_filename": None,
    "artifact_path": "org-1/deployments/dep-1/runs/run-a1.tgz",
    "artifact_bytes": 1234,
    "artifact_sha256": "deadbeef" * 8,
}


def test_redeploy_uses_archived_bytes(client, make_token, monkeypatch):
    launched = {}
    _dep_visible(monkeypatch)
    monkeypatch.setattr("app.deploy.get_run", lambda settings, run_id: dict(ARCHIVED_RUN))
    monkeypatch.setattr(
        "app.deploy.create_derived_run",
        lambda settings, dep, src, relation, by, email: f"run-{relation}",
    )
    monkeypatch.setattr("app.deploy.launch", lambda settings, ctx: launched.update(ctx))

    resp = client.post(
        "/api/v1/deployments/dep-1/runs/run-a1/redeploy", headers=_auth(make_token)
    )
    assert resp.status_code == 202
    assert resp.json()["run_id"] == "run-redeploy"
    assert launched["archive"]["path"] == ARCHIVED_RUN["artifact_path"]
    assert launched["archive"]["ext"] == "tgz"


def test_redeploy_without_artifact_is_409(client, make_token, monkeypatch):
    _dep_visible(monkeypatch)
    monkeypatch.setattr(
        "app.deploy.get_run",
        lambda settings, run_id: {**ARCHIVED_RUN, "artifact_path": None},
    )
    resp = client.post(
        "/api/v1/deployments/dep-1/runs/run-a1/redeploy", headers=_auth(make_token)
    )
    assert resp.status_code == 409


def test_artifact_download_streams_bytes(client, make_token, monkeypatch):
    _dep_visible(monkeypatch)
    monkeypatch.setattr("app.deploy.get_run", lambda settings, run_id: dict(ARCHIVED_RUN))

    async def fake_get_object(settings, path):
        assert path == ARCHIVED_RUN["artifact_path"]
        return b"tarball-bytes"

    monkeypatch.setattr("app.routers.deployments.storage.get_object", fake_get_object)
    resp = client.get(
        "/api/v1/deployments/dep-1/runs/run-a1/artifact", headers=_auth(make_token)
    )
    assert resp.status_code == 200
    assert resp.content == b"tarball-bytes"
    assert "run-a1.tgz" in resp.headers["content-disposition"]


def test_artifact_download_rejects_crafted_path(client, make_token, monkeypatch):
    """The endpoint must never serve paths outside the deployment's runs/
    folder — e.g. a run row pointing at server credentials."""
    _dep_visible(monkeypatch)
    monkeypatch.setattr(
        "app.deploy.get_run",
        lambda settings, run_id: {
            **ARCHIVED_RUN,
            "artifact_path": "org-1/servers/srv-1/ssh_key",
        },
    )
    resp = client.get(
        "/api/v1/deployments/dep-1/runs/run-a1/artifact", headers=_auth(make_token)
    )
    assert resp.status_code == 404


# --- promote (US-1.43) ----------------------------------------------------------


def test_promote_pins_commit_via_target_pipeline(client, make_token, monkeypatch):
    launched = {}
    target_row = {
        **DEPLOYMENT_ROW,
        "id": "dep-2",
        "name": "production",
        "project_id": "proj-1",
    }

    async def fake_get(settings, token, table, params):
        if table == "deployments":
            if params["id"] == "eq.dep-1":
                return [DEPLOYMENT_ROW]
            return [target_row]
        return [{"id": "c1", "method": "app", "installation_id": 42,
                  "vault_secret_id": None, "repos": []}]

    async def fake_mint(settings, installation_id):
        return "tok"

    async def fake_commit(token, owner, repo, ref):
        return {"sha": "abc1234def", "commit": {"message": "fix things"}}

    monkeypatch.setattr("app.routers.deployments.postgrest_get", fake_get)
    monkeypatch.setattr("app.github_tokens.postgrest_get", fake_get)
    monkeypatch.setattr("app.github_tokens.github.mint_installation_token", fake_mint)
    monkeypatch.setattr("app.github.get_commit", fake_commit)
    monkeypatch.setattr("app.deploy.get_run", lambda settings, run_id: dict(ARCHIVED_RUN))
    monkeypatch.setattr(
        "app.deploy.create_derived_run",
        lambda settings, dep, src, relation, by, email: "run-promoted",
    )
    monkeypatch.setattr("app.deploy.launch", lambda settings, ctx: launched.update(ctx))

    resp = client.post(
        "/api/v1/deployments/dep-1/runs/run-a1/promote",
        json={"target_deployment_id": "dep-2"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 202
    assert launched["deployment"]["id"] == "dep-2"  # target's pipeline + rules
    assert launched["override"]["sha"] == "abc1234def"
    assert launched["archive"] is None  # commit still fetchable -> re-fetch by SHA


def test_promote_cross_project_rejected(client, make_token, monkeypatch):
    async def fake_get(settings, token, table, params):
        if params["id"] == "eq.dep-1":
            return [DEPLOYMENT_ROW]
        return [{**DEPLOYMENT_ROW, "id": "dep-9", "project_id": "proj-OTHER"}]

    monkeypatch.setattr("app.routers.deployments.postgrest_get", fake_get)
    monkeypatch.setattr("app.deploy.get_run", lambda settings, run_id: dict(ARCHIVED_RUN))

    resp = client.post(
        "/api/v1/deployments/dep-1/runs/run-a1/promote",
        json={"target_deployment_id": "dep-9"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 400
    assert "same project" in resp.json()["detail"]


# --- protected deployments (US-1.41) ------------------------------------------


def _protected_dep_visible(monkeypatch, is_owner):
    async def fake_get(settings, token, table, params):
        return [{**DEPLOYMENT_ROW, "protected": True}]

    async def fake_rpc(settings, token, fn, args):
        assert fn == "is_org_owner"
        return is_owner

    monkeypatch.setattr("app.routers.deployments.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.deployments.rpc", fake_rpc)


def test_protected_run_rejected_for_member(client, make_token, monkeypatch):
    _protected_dep_visible(monkeypatch, is_owner=False)
    resp = client.post("/api/v1/deployments/dep-1/run", headers=_auth(make_token))
    assert resp.status_code == 403
    assert "owners only" in resp.json()["detail"].lower()


def test_protected_delete_rejected_for_member(client, make_token, monkeypatch):
    _protected_dep_visible(monkeypatch, is_owner=False)
    resp = client.delete("/api/v1/deployments/dep-1", headers=_auth(make_token))
    assert resp.status_code == 403


def test_protected_env_write_rejected_for_member(client, make_token, monkeypatch):
    _protected_dep_visible(monkeypatch, is_owner=False)
    resp = client.put(
        "/api/v1/deployments/dep-1/env/X",
        json={"value": "v"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 403


def test_protected_run_allowed_for_owner(client, make_token, monkeypatch):
    _protected_dep_visible(monkeypatch, is_owner=True)
    monkeypatch.setattr(
        "app.deploy.create_run",
        lambda settings, dep, by, email, source="branch", zip_filename=None, branch_override=None: "run-p1",
    )
    monkeypatch.setattr("app.deploy.launch", lambda settings, ctx: None)
    resp = client.post("/api/v1/deployments/dep-1/run", headers=_auth(make_token))
    assert resp.status_code == 202


# --- health check (US-1.40) ----------------------------------------------------


def test_manual_health_check_requires_config(client, make_token, monkeypatch):
    _dep_visible(monkeypatch)  # DEPLOYMENT_ROW has no health_check_url
    resp = client.post(
        "/api/v1/deployments/dep-1/health-check", headers=_auth(make_token)
    )
    assert resp.status_code == 400


def test_manual_health_check_reports_result(client, make_token, monkeypatch):
    class FakeConn:
        transport = object()

        def close(self):
            pass

    async def fake_get(settings, token, table, params):
        return [
            {**DEPLOYMENT_ROW, "health_check_url": "http://localhost/health",
             "health_check_expected_status": 200}
        ]

    async def fake_connect(settings, server):
        return FakeConn()

    monkeypatch.setattr("app.routers.deployments.postgrest_get", fake_get)
    monkeypatch.setattr("app.deploy.connect_to_server", fake_connect)
    monkeypatch.setattr(
        "app.deploy.health_check_once",
        lambda transport, url, expected: (True, "HTTP 200"),
    )

    resp = client.post(
        "/api/v1/deployments/dep-1/health-check", headers=_auth(make_token)
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "last": "HTTP 200"}


# --- cancel (US-1.35) --------------------------------------------------------


def test_cancel_active_run(client, make_token, monkeypatch):
    cancelled = {}
    _dep_visible(monkeypatch)
    monkeypatch.setattr(
        "app.deploy.get_run",
        lambda settings, run_id: {"id": run_id, "deployment_id": "dep-1", "status": "running"},
    )
    monkeypatch.setattr(
        "app.deploy.request_cancel",
        lambda settings, run_id, by, email: cancelled.update(run=run_id) or "signalled",
    )
    resp = client.post(
        "/api/v1/deployments/dep-1/runs/run-5/cancel", headers=_auth(make_token)
    )
    assert resp.status_code == 200
    assert resp.json()["outcome"] == "signalled"
    assert cancelled["run"] == "run-5"


def test_cancel_finished_run_is_409(client, make_token, monkeypatch):
    _dep_visible(monkeypatch)
    monkeypatch.setattr(
        "app.deploy.get_run",
        lambda settings, run_id: {"id": run_id, "deployment_id": "dep-1", "status": "succeeded"},
    )
    monkeypatch.setattr(
        "app.deploy.request_cancel", lambda settings, run_id, by, email: "not-active"
    )
    resp = client.post(
        "/api/v1/deployments/dep-1/runs/run-5/cancel", headers=_auth(make_token)
    )
    assert resp.status_code == 409


def test_cancel_foreign_run_is_404(client, make_token, monkeypatch):
    _dep_visible(monkeypatch)
    monkeypatch.setattr(
        "app.deploy.get_run",
        lambda settings, run_id: {"id": run_id, "deployment_id": "dep-OTHER", "status": "running"},
    )
    resp = client.post(
        "/api/v1/deployments/dep-1/runs/run-5/cancel", headers=_auth(make_token)
    )
    assert resp.status_code == 404


# --- drift (US-1.34) ---------------------------------------------------------


def _drift_setup(monkeypatch, run, compare=None, compare_error=None):
    async def fake_get(settings, token, table, params):
        if table == "deployments":
            return [{**DEPLOYMENT_ROW, "current_run_id": "run-1"}]
        assert table == "github_connections"
        return [{"id": "c1", "method": "app", "installation_id": 42,
                  "vault_secret_id": None, "repos": []}]

    async def fake_mint(settings, installation_id):
        return "inst-token"

    async def fake_compare(token, owner, repo, base, head):
        if compare_error:
            raise compare_error
        return compare

    monkeypatch.setattr("app.routers.deployments.postgrest_get", fake_get)
    monkeypatch.setattr("app.github_tokens.postgrest_get", fake_get)
    monkeypatch.setattr("app.github_tokens.github.mint_installation_token", fake_mint)
    monkeypatch.setattr("app.github.compare_commits", fake_compare)
    monkeypatch.setattr("app.deploy.get_run", lambda settings, run_id: run)


def test_drift_never_deployed(client, make_token, monkeypatch):
    _dep_visible(monkeypatch)  # DEPLOYMENT_ROW has no current_run_id
    resp = client.get("/api/v1/deployments/dep-1/drift", headers=_auth(make_token))
    assert resp.status_code == 200
    assert resp.json() == {"state": "never"}


def test_drift_zip_payload_is_na(client, make_token, monkeypatch):
    _drift_setup(monkeypatch, {"source": "zip", "commit_sha": None})
    resp = client.get("/api/v1/deployments/dep-1/drift", headers=_auth(make_token))
    assert resp.json() == {"state": "zip"}


def test_drift_behind_lists_commits(client, make_token, monkeypatch):
    _drift_setup(
        monkeypatch,
        {"source": "branch", "commit_sha": "abc"},
        compare={
            "status": "ahead",
            "ahead_by": 2,
            "commits": [
                {"sha": "c1", "commit": {"message": "older fix", "author": {"name": "a", "date": "d1"}}},
                {"sha": "c2", "commit": {"message": "newer fix", "author": {"name": "b", "date": "d2"}}},
            ],
        },
    )
    resp = client.get("/api/v1/deployments/dep-1/drift", headers=_auth(make_token))
    body = resp.json()
    assert body["state"] == "behind"
    assert body["behind_by"] == 2
    assert body["commits"][0]["sha"] == "c2"  # newest first


def test_drift_diverged_history(client, make_token, monkeypatch):
    _drift_setup(
        monkeypatch,
        {"source": "branch", "commit_sha": "abc"},
        compare={"status": "diverged"},
    )
    resp = client.get("/api/v1/deployments/dep-1/drift", headers=_auth(make_token))
    assert resp.json() == {"state": "diverged"}


def test_drift_github_error_is_502(client, make_token, monkeypatch):
    from app.github import GitHubError

    _drift_setup(
        monkeypatch,
        {"source": "branch", "commit_sha": "abc"},
        compare_error=GitHubError("boom"),
    )
    resp = client.get("/api/v1/deployments/dep-1/drift", headers=_auth(make_token))
    assert resp.status_code == 502


# --- env vars (US-1.37): write-only values ----------------------------------


def _dep_visible(monkeypatch):
    async def fake_get(settings, token, table, params):
        assert table == "deployments"
        return [DEPLOYMENT_ROW]

    monkeypatch.setattr("app.routers.deployments.postgrest_get", fake_get)


def test_set_env_var_validates_name(client, make_token, monkeypatch):
    _dep_visible(monkeypatch)
    resp = client.put(
        "/api/v1/deployments/dep-1/env/1BAD-NAME",
        json={"value": "s3cret"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 400
    assert "POSIX" in resp.json()["detail"]


def test_set_env_var_writes_bucket_and_row_never_echoes(
    client, make_token, monkeypatch
):
    written = {}

    async def fake_put(settings, path, content, content_type="application/octet-stream"):
        written[path] = content

    def fake_upsert(settings, org_id, deployment_id, name, actor="api"):
        written["row"] = (org_id, deployment_id, name)
        written["actor"] = actor

    _dep_visible(monkeypatch)
    monkeypatch.setattr("app.routers.deployments.storage.put_object", fake_put)
    monkeypatch.setattr("app.deploy.upsert_env_var", fake_upsert)

    resp = client.put(
        "/api/v1/deployments/dep-1/env/DB_PASSWORD",
        json={"value": "hunter2-secret"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 200
    assert written["org-1/deployments/dep-1/env/DB_PASSWORD"] == b"hunter2-secret"
    assert written["row"] == ("org-1", "dep-1", "DB_PASSWORD")
    # the secret must never appear in the response
    assert "hunter2-secret" not in resp.text


def test_remove_env_var_deletes_object_and_row(client, make_token, monkeypatch):
    deleted = {}

    async def fake_delete_object(settings, path):
        deleted["object"] = path

    def fake_delete_row(settings, deployment_id, name, org_id=None, actor="api"):
        deleted["row"] = (deployment_id, name)

    _dep_visible(monkeypatch)
    monkeypatch.setattr(
        "app.routers.deployments.storage.delete_object", fake_delete_object
    )
    monkeypatch.setattr("app.deploy.delete_env_var", fake_delete_row)

    resp = client.delete(
        "/api/v1/deployments/dep-1/env/DB_PASSWORD", headers=_auth(make_token)
    )
    assert resp.status_code == 200
    assert deleted["object"] == "org-1/deployments/dep-1/env/DB_PASSWORD"
    assert deleted["row"] == ("dep-1", "DB_PASSWORD")


def test_masker_hides_values():
    from app.deploy import make_masker

    mask = make_masker({"A": "hunter2-secret", "B": "xy"})  # "xy" too short
    assert mask("password is hunter2-secret ok") == "password is •••• ok"
    assert mask("xy stays") == "xy stays"
    assert mask("nothing here") == "nothing here"


# --- payload-aware disk preflight (US-2.14) ----------------------------------


def test_required_free_unknown_size_uses_floor():
    from app.deploy import PREFLIGHT_MIN_FREE_MB, compute_required_free_mb

    need, why = compute_required_free_mb(None, "in-place", 5)
    assert need == PREFLIGHT_MIN_FREE_MB
    assert "floor" in why.lower()


def test_required_free_known_size_needs_archive_plus_extracted():
    from app.deploy import compute_required_free_mb

    # 400 MB payload, in-place: archive + one extracted copy ~= 2x + margin
    need, why = compute_required_free_mb(400 * 1_048_576, "in-place", 5)
    assert need >= 800
    assert "400" in why  # states the payload size it checked against


def test_required_free_releases_strategy_accounts_for_retention():
    from app.deploy import compute_required_free_mb

    in_place, _ = compute_required_free_mb(100 * 1_048_576, "in-place", 5)
    releases, why = compute_required_free_mb(100 * 1_048_576, "releases", 5)
    # the new release lands alongside retained ones before prune
    assert releases > in_place
    assert "retain" in why.lower() or "release" in why.lower()


def test_required_free_small_known_size_still_respects_floor():
    from app.deploy import PREFLIGHT_MIN_FREE_MB, compute_required_free_mb

    need, _ = compute_required_free_mb(1 * 1_048_576, "in-place", 5)
    assert need >= PREFLIGHT_MIN_FREE_MB  # tiny payload never drops below the floor


# --- preflight (US-1.38) -----------------------------------------------------


def test_preflight_reports_results(client, make_token, monkeypatch):
    class FakeConn:
        transport = object()

        def close(self):
            pass

    async def fake_connect(settings, server):
        return FakeConn()

    def fake_checks(
        transport, target, min_free_mb=200, tools=("tar",), space_reason=None
    ):
        assert target == "/var/www/app"
        return [
            {"check": "ssh", "ok": True, "detail": "Connected"},
            {"check": "tool-tar", "ok": False, "detail": "`tar` is not installed on this server"},
        ]

    _dep_visible(monkeypatch)
    monkeypatch.setattr("app.deploy.connect_to_server", fake_connect)
    monkeypatch.setattr("app.deploy.preflight_checks", fake_checks)

    resp = client.post(
        "/api/v1/deployments/dep-1/preflight", headers=_auth(make_token)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["results"][1]["check"] == "tool-tar"


def test_preflight_connect_failure_is_a_result(client, make_token, monkeypatch):
    from app.deploy import PipelineError

    async def fake_connect(settings, server):
        raise PipelineError("authentication failed")

    _dep_visible(monkeypatch)
    monkeypatch.setattr("app.deploy.connect_to_server", fake_connect)

    resp = client.post(
        "/api/v1/deployments/dep-1/preflight", headers=_auth(make_token)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["results"][0]["check"] == "ssh"
    assert "authentication failed" in body["results"][0]["detail"]


# --- deployment delete cleans the bucket (US-1.37) ---------------------------


def test_delete_deployment_removes_bucket_prefix(client, make_token, monkeypatch):
    removed = []

    async def fake_delete(settings, token, table, params):
        assert table == "deployments"

    async def fake_delete_prefix(settings, prefix):
        removed.append(prefix)

    _dep_visible(monkeypatch)
    monkeypatch.setattr("app.routers.deployments.postgrest_delete", fake_delete)
    monkeypatch.setattr(
        "app.routers.deployments.storage.delete_prefix", fake_delete_prefix
    )

    resp = client.delete("/api/v1/deployments/dep-1", headers=_auth(make_token))
    assert resp.status_code == 200
    assert "org-1/deployments/dep-1/env" in removed
    assert "org-1/deployments/dep-1" in removed


def test_delete_server_ok_when_unreferenced(client, make_token, monkeypatch):
    async def fake_get(settings, token, table, params):
        return [{"id": "srv-1", "org_id": "org-1", "name": "web-1"}]

    async def fake_delete(settings, token, table, params):
        return None

    async def fake_delete_prefix(settings, prefix):
        return None

    monkeypatch.setattr("app.routers.servers.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.servers.postgrest_delete", fake_delete)
    monkeypatch.setattr(
        "app.routers.servers.storage.delete_prefix", fake_delete_prefix
    )

    resp = client.delete("/api/v1/servers/srv-1", headers=_auth(make_token))
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


# --- the pre-flight read every operation shares (BUG-1.1) --------------------
#
# `get_deployment_for_user` is the single gate in front of every endpoint in
# this router. When US-16.1's `app_issues` gave PostgREST a second route from
# `deployments` to `projects`, that one read started answering 300 Multiple
# Choices and took all sixteen operations down together — but only the delete
# button was ever clicked, so it was filed as a delete bug. These tests
# exercise the whole set, so the next time it breaks it says so sixteen times.


# Every operation, addressed the way the UI addresses it. The pre-flight read
# runs before anything else in each one, so none of them needs its downstream
# mocked here.
OPERATIONS = [
    ("post", "/api/v1/deployments/dep-1/run", {}),
    ("post", "/api/v1/deployments/dep-1/agent-dispatch", {}),
    ("post", "/api/v1/deployments/dep-1/duplicate", {"json": {"name": "copy"}}),
    ("post", "/api/v1/deployments/dep-1/runs/run-1/cancel", {}),
    ("get", "/api/v1/deployments/dep-1/drift", {}),
    (
        "post",
        "/api/v1/deployments/dep-1/zip",
        {"files": {"file": ("build.zip", b"PK\x03\x04", "application/zip")}},
    ),
    ("post", "/api/v1/deployments/dep-1/redeploy-zip", {}),
    ("post", "/api/v1/deployments/dep-1/runs/run-1/redeploy", {}),
    (
        "post",
        "/api/v1/deployments/dep-1/runs/run-1/promote",
        {"json": {"target_deployment_id": "dep-2"}},
    ),
    ("get", "/api/v1/deployments/dep-1/runs/run-1/artifact", {}),
    ("post", "/api/v1/deployments/dep-1/rollback", {"json": {"run_id": "run-1"}}),
    ("post", "/api/v1/deployments/dep-1/preflight", {}),
    ("post", "/api/v1/deployments/dep-1/health-check", {}),
    ("put", "/api/v1/deployments/dep-1/env/TOKEN", {"json": {"value": "s3cret"}}),
    ("delete", "/api/v1/deployments/dep-1/env/TOKEN", {}),
    ("delete", "/api/v1/deployments/dep-1", {}),
]


def _call(client, make_token, operation):
    method, url, kwargs = operation
    return getattr(client, method)(url, headers=_auth(make_token), **kwargs)


@pytest.mark.parametrize("operation", OPERATIONS, ids=lambda op: f"{op[0]} {op[1]}")
def test_every_operation_names_the_project_relationship(
    client, make_token, monkeypatch, operation
):
    """The embed has to say WHICH relationship: `app_issues` holds NOT NULL
    foreign keys to both `deployments` and `projects`, so PostgREST sees two
    ways across and refuses to pick."""
    selects = []

    async def fake_get(settings, token, table, params):
        selects.append(params["select"])
        return []  # a 404 is enough; we are here for the query it sent

    monkeypatch.setattr("app.routers.deployments.postgrest_get", fake_get)
    _call(client, make_token, operation)

    assert selects, "the operation did not run the shared pre-flight read"
    for select in selects:
        assert "projects!deployments_project_id_org_id_fkey(" in select
        assert ",projects(" not in select


def _refusal(status: int, body: dict) -> httpx.Response:
    request = httpx.Request(
        "GET", "https://test.supabase.co/rest/v1/deployments?select=*"
    )
    return httpx.Response(status, request=request, json=body)


AMBIGUOUS_EMBED = {
    "code": "PGRST201",
    "details": [
        {
            "cardinality": "many-to-one",
            "relationship": "deployments_project_id_org_id_fkey",
            "embedding": "deployments with projects",
        },
        {
            "cardinality": "many-to-many",
            "relationship": "app_issues",
            "embedding": "deployments with projects",
        },
    ],
    "hint": "Try changing 'projects' to one of the following:"
    " 'projects!deployments_project_id_org_id_fkey', 'projects!app_issues'.",
    "message": "Could not embed because more than one relationship was found"
    " for 'deployments' and 'projects'",
}


@pytest.mark.parametrize("operation", OPERATIONS, ids=lambda op: f"{op[0]} {op[1]}")
def test_a_refused_pre_flight_read_says_why(
    client, make_token, monkeypatch, operation
):
    """The reported symptom was "API error 500" with nothing behind it. A
    refusal now arrives as a 502 carrying PostgREST's code and message —
    which the dialog already renders, because it renders `detail`."""
    from app.supabase import PostgrestQueryError

    async def fake_get(settings, token, table, params):
        raise PostgrestQueryError(_refusal(300, AMBIGUOUS_EMBED))

    monkeypatch.setattr("app.routers.deployments.postgrest_get", fake_get)
    monkeypatch.setattr("app.app_issues.self_report", lambda *a, **k: None)

    resp = _call(client, make_token, operation)
    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert "PGRST201" in detail
    assert "more than one relationship" in detail


def test_a_refused_read_is_still_reported_as_a_system_error(
    client, make_token, monkeypatch
):
    """Translating the answer must not empty the crash inbox — this bug was
    found there, and a nicer 502 that nobody records is a worse trade."""
    from app.supabase import PostgrestQueryError

    reported = []

    async def fake_get(settings, token, table, params):
        raise PostgrestQueryError(_refusal(300, AMBIGUOUS_EMBED))

    monkeypatch.setattr("app.routers.deployments.postgrest_get", fake_get)
    monkeypatch.setattr(
        "app.app_issues.self_report",
        lambda settings, exc, context=None: reported.append((exc, context)),
    )

    resp = client.delete("/api/v1/deployments/dep-1", headers=_auth(make_token))
    assert resp.status_code == 502
    assert len(reported) == 1
    exc, context = reported[0]
    assert exc.code == "PGRST201"
    assert context["path"] == "/api/v1/deployments/dep-1"
    assert context["method"] == "DELETE"


# --- the delete itself, through the real PostgREST client --------------------


def _supabase_responses(monkeypatch, handler):
    """Point the PostgREST client at canned answers without replacing it —
    the real request/response plumbing runs, which is where the 300 was
    being mishandled."""
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        return real_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("app.supabase.httpx.AsyncClient", factory)


def test_delete_completes_against_a_supabase_that_answers(
    client, make_token, monkeypatch
):
    """The end the bug report asked for: click delete, the row goes."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url)))
        if request.method == "GET":
            return httpx.Response(200, json=[DEPLOYMENT_ROW])
        return httpx.Response(204)

    async def fake_delete_prefix(settings, prefix):
        return None

    _supabase_responses(monkeypatch, handler)
    monkeypatch.setattr(
        "app.routers.deployments.storage.delete_prefix", fake_delete_prefix
    )

    resp = client.delete("/api/v1/deployments/dep-1", headers=_auth(make_token))
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    methods = [method for method, _ in seen]
    assert methods == ["GET", "DELETE"]
    assert "deployments_project_id_org_id_fkey" in seen[0][1]


def test_delete_reports_an_ambiguous_embed_instead_of_crashing(
    client, make_token, monkeypatch
):
    """The exact production failure, reproduced through the real client: the
    pre-flight read answers 300, and the caller learns what happened."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(300, json=AMBIGUOUS_EMBED)

    _supabase_responses(monkeypatch, handler)
    monkeypatch.setattr("app.app_issues.self_report", lambda *a, **k: None)

    resp = client.delete("/api/v1/deployments/dep-1", headers=_auth(make_token))
    assert resp.status_code == 502
    assert "PGRST201" in resp.json()["detail"]
