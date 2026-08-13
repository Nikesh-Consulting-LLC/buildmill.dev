"""US-79.2: a failed merge names its credential and its cure (prod BUG-2/3).

Both prod bugs were one approve on the Demo project's PR #31: GitHub's 401
("Bad credentials") and 404 ("Not Found") were relayed as bare 409s with no
hint of which credential was even used — and a credential-resolution failure
was mislabeled "No GitHub connection" when the connection existed and its
secret was the broken part.
"""

import uuid

import pytest

from app import github, github_tokens

RUN_ID = str(uuid.uuid4())
PR_URL = "https://github.com/acme/demo/pull/31"


def _auth(make_token):
    return {"Authorization": f"Bearer {make_token()}"}


@pytest.fixture()
def approve_ready(monkeypatch):
    """A code run in review with a real PR; the precheck agrees."""

    async def fake_get(settings, token, path, params):
        if path == "runs":
            return [
                {
                    "pr_url": PR_URL,
                    "issue_id": "issue-1",
                    "kind": "code",
                    "org_id": "org-1",
                }
            ]
        if path == "issues":
            return [{"status": "in-review", "github_issue_number": None}]
        raise AssertionError(f"unexpected path {path}")

    async def fake_rpc(settings, token, fn, args):
        return None

    async def fake_patch(settings, token, path, match, body):
        fake_patch.calls.append((path, body))

    fake_patch.calls = []

    monkeypatch.setattr("app.routers.reviews.postgrest_get", fake_get)
    monkeypatch.setattr("app.issue_sync.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.reviews.rpc", fake_rpc)
    monkeypatch.setattr("app.routers.reviews.postgrest_patch", fake_patch)
    return fake_patch


@pytest.fixture()
def credential(monkeypatch):
    async def fake_resolve(settings, user_token, org_id, repo_full_name=None):
        return "ghp_secret_token_dead", "the stored PAT (…dead)"

    monkeypatch.setattr(
        "app.routers.reviews.github_tokens.resolve_for_user", fake_resolve
    )
    return "the stored PAT (…dead)"


@pytest.fixture()
def reports(monkeypatch):
    from app import app_issues

    captured: list[dict] = []

    def _ingest(settings, deployment, payload):
        captured.append(payload)
        return {"id": "77777777-7777-4777-8777-777777777777", "deduped": False}

    monkeypatch.setattr(
        app_issues,
        "_self_deployment",
        lambda settings: {"id": "d", "org_id": "o", "project_id": "p"},
    )
    monkeypatch.setattr(app_issues, "ingest_report", _ingest)
    return captured


def test_a_401_names_the_credential_and_the_cure(
    client, make_token, monkeypatch, approve_ready, credential, reports
):
    async def merge_401(token, owner, repo, number, merge_method="squash"):
        raise github.GitHubError(
            "GitHub merge failed: Bad credentials", upstream_status=401
        )

    monkeypatch.setattr("app.routers.reviews.github.merge_pull_request", merge_401)
    resp = client.post(f"/api/v1/runs/{RUN_ID}/approve", headers=_auth(make_token))
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert "the stored PAT (…dead)" in detail
    assert "Settings → GitHub" in detail
    assert "ghp_secret_token_dead" not in detail, "the secret leaked into the detail"
    # Still a dependency failure: reported, with the diagnosis in context.
    # The key is "connection", not "credential" — the scrubber redacts any
    # credential-named key, and this value is a description, never a secret.
    assert len(reports) == 1
    assert reports[0]["context"]["connection"] == credential
    assert "ghp_secret_token_dead" not in repr(reports)


def test_a_hand_merged_pr_reconciles_as_success(
    client, make_token, monkeypatch, approve_ready, credential, reports
):
    async def merge_404(token, owner, repo, number, merge_method="squash"):
        raise github.GitHubError(
            "GitHub merge failed: Not Found", upstream_status=404
        )

    async def pull_merged(token, owner, repo, number):
        return {"merged": True, "state": "closed", "merge_commit_sha": "cafebabe"}

    monkeypatch.setattr("app.routers.reviews.github.merge_pull_request", merge_404)
    monkeypatch.setattr("app.routers.reviews.github.get_pull", pull_merged)
    resp = client.post(f"/api/v1/runs/{RUN_ID}/approve", headers=_auth(make_token))
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "merge": "already-merged"}
    # Traceability: the real merge SHA is recorded exactly as a fresh merge's.
    assert ("runs", {"merge_commit_sha": "cafebabe"}) in approve_ready.calls
    assert reports == [], "the outcome approve wanted is not a defect"


def test_a_hand_closed_pr_says_reopen_or_reject(
    client, make_token, monkeypatch, approve_ready, credential, reports
):
    async def merge_404(token, owner, repo, number, merge_method="squash"):
        raise github.GitHubError(
            "GitHub merge failed: Not Found", upstream_status=404
        )

    async def pull_closed(token, owner, repo, number):
        return {"merged": False, "state": "closed", "merge_commit_sha": None}

    monkeypatch.setattr("app.routers.reviews.github.merge_pull_request", merge_404)
    monkeypatch.setattr("app.routers.reviews.github.get_pull", pull_closed)
    resp = client.post(f"/api/v1/runs/{RUN_ID}/approve", headers=_auth(make_token))
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert "closed on GitHub without merging" in detail
    assert "reopen" in detail and "reject" in detail


def test_an_invisible_pr_names_the_credential_and_the_grant(
    client, make_token, monkeypatch, approve_ready, credential, reports
):
    async def merge_404(token, owner, repo, number, merge_method="squash"):
        raise github.GitHubError(
            "GitHub merge failed: Not Found", upstream_status=404
        )

    async def pull_404(token, owner, repo, number):
        raise github.GitHubError("pull request not found (404)", upstream_status=404)

    monkeypatch.setattr("app.routers.reviews.github.merge_pull_request", merge_404)
    monkeypatch.setattr("app.routers.reviews.github.get_pull", pull_404)
    resp = client.post(f"/api/v1/runs/{RUN_ID}/approve", headers=_auth(make_token))
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert "acme/demo#31" in detail
    assert "the stored PAT (…dead)" in detail
    assert "repository access" in detail
    assert len(reports) == 1
    assert reports[0]["context"]["upstream_status"] == 404


def test_a_missing_vault_secret_is_not_called_no_connection(
    client, make_token, monkeypatch, approve_ready, reports
):
    async def broken_resolve(settings, user_token, org_id, repo_full_name=None):
        raise github.GitHubCredentialError(
            "stored GitHub token missing from the vault — the manager must "
            "reconnect GitHub in Settings → GitHub"
        )

    monkeypatch.setattr(
        "app.routers.reviews.github_tokens.resolve_for_user", broken_resolve
    )
    resp = client.post(f"/api/v1/runs/{RUN_ID}/approve", headers=_auth(make_token))
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert "vault" in detail
    assert "no GitHub connection" not in detail, (
        "a broken credential was mislabeled as a missing connection"
    )
    assert len(reports) == 1, "a broken credential is a dependency failure"


def test_no_connection_at_all_keeps_its_own_message(
    client, make_token, monkeypatch, approve_ready, reports
):
    async def none_resolve(settings, user_token, org_id, repo_full_name=None):
        raise github.GitHubNotConfigured(
            "the org has no GitHub connection — the manager must connect "
            "one in Settings → GitHub"
        )

    monkeypatch.setattr(
        "app.routers.reviews.github_tokens.resolve_for_user", none_resolve
    )
    resp = client.post(f"/api/v1/runs/{RUN_ID}/approve", headers=_auth(make_token))
    assert resp.status_code == 409
    assert "no GitHub connection" in resp.json()["detail"]
    assert reports == [], "unconfigured is the manager's to-do, not a defect"


# --- the credential described, never echoed ----------------------------------


def test_describe_connection_covers_every_method():
    assert (
        github_tokens.describe_connection(None, "tok")
        == "the GITHUB_TOKEN environment fallback"
    )
    assert github_tokens.describe_connection(
        {"method": "app", "installation_id": 42}, "tok"
    ) == "the org's GitHub App installation (id 42)"
    described = github_tokens.describe_connection(
        {"method": "pat"}, "ghp_secret_value_abcd"
    )
    assert described == "the stored PAT (…abcd)"
    assert "ghp_secret_value" not in described
