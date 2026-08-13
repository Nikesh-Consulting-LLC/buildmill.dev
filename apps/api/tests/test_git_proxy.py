"""US-3.8: factory git remote — auth, org scoping, push policy, streaming.

Endpoint-level with the upstream seam patched; the real-git-client
integration lives in test_git_proxy_integration.py.
"""

import base64
import uuid

import pytest

from app.routers import gitproxy

# Captured before any fixture patches it out — the credential-refresh tests
# need the real resolver, not git_auth's stub.
REAL_REPO_TOKEN = gitproxy._repo_token

PROJECT_ID = str(uuid.uuid4())
ISSUE_ID = str(uuid.uuid4())
RUN_ID = str(uuid.uuid4())
ORG_ID = str(uuid.uuid4())

# US-3.13: the remote is addressed by org shortname + project slug.
ORG_SHORTNAME = "acme"
PROJECT_SLUG = "webshop"
GIT_PATH = f"/git/{ORG_SHORTNAME}/{PROJECT_SLUG}.git"

WORKER = {
    "id": str(uuid.uuid4()),
    "org_id": ORG_ID,
    "name": "pool-test-a",
    "type": "autonomous",
    "status": "active",
}

ZEROS = "0" * 40
SHA_A = "a" * 40
SHA_B = "b" * 40


def _basic(token="sfw_testtoken", user="worker"):
    raw = base64.b64encode(f"{user}:{token}".encode()).decode()
    return {"Authorization": f"Basic {raw}"}


def pkt(data: bytes) -> bytes:
    return f"{len(data) + 4:04x}".encode() + data


FLUSH = b"0000"


def push_body(old: str, new: str, ref: str, pack: bytes = b"PACK-fake-data") -> bytes:
    line = (
        f"{old} {new} {ref}".encode()
        + b"\x00report-status side-band-64k agent=git/2.40.0\n"
    )
    return pkt(line) + FLUSH + pack


@pytest.fixture
def git_auth(monkeypatch):
    def fake_lookup(settings, token):
        return dict(WORKER) if token == "sfw_testtoken" else None

    monkeypatch.setattr("app.routers.gitproxy.db.get_worker_by_token", fake_lookup)
    monkeypatch.setattr(
        "app.routers.gitproxy.db.get_project_repo",
        lambda s, shortname, slug, org_id: {
            "id": PROJECT_ID,
            "repo_full_name": "acme/webshop",
        }
        if shortname == ORG_SHORTNAME and slug == PROJECT_SLUG and org_id == ORG_ID
        else None,
    )

    async def fake_repo_token(settings, org_id, repo_full_name, fresh=False):
        return "ghs_upstream_token"

    monkeypatch.setattr("app.routers.gitproxy._repo_token", fake_repo_token)
    # US-3.12: unrestricted by default; capability tests override this.
    monkeypatch.setattr(
        "app.routers.gitproxy.db.worker_allowed_for_project",
        lambda s, w, p: True,
    )
    return WORKER


@pytest.fixture
def upstream(monkeypatch):
    captured = {"calls": []}

    async def fake_upstream(method, url, headers, content=None):
        body = b""
        if content is not None:
            async for chunk in content:
                body += chunk
        captured["calls"].append(
            {"method": method, "url": url, "headers": headers, "body": body}
        )

        async def it():
            yield captured.get("response", b"0008ok\n0000")

        return (
            200,
            {"content-type": captured.get("ctype", "application/x-git-result")},
            it(),
        )

    monkeypatch.setattr("app.routers.gitproxy._upstream_stream", fake_upstream)
    return captured


def test_info_refs_requires_basic_auth(client, git_auth, upstream):
    resp = client.get(f"{GIT_PATH}/info/refs?service=git-upload-pack")
    assert resp.status_code == 401
    assert resp.headers.get("www-authenticate", "").startswith("Basic")

    resp = client.get(
        f"{GIT_PATH}/info/refs?service=git-upload-pack",
        headers=_basic(token="sfw_wrong"),
    )
    assert resp.status_code == 401


def test_unknown_or_cross_org_project_is_404(client, git_auth, upstream):
    # unknown slug within the worker's org
    resp = client.get(
        f"/git/{ORG_SHORTNAME}/nope.git/info/refs?service=git-upload-pack",
        headers=_basic(),
    )
    assert resp.status_code == 404
    # someone else's org shortname — even with a valid slug
    resp = client.get(
        f"/git/other-org/{PROJECT_SLUG}.git/info/refs?service=git-upload-pack",
        headers=_basic(),
    )
    assert resp.status_code == 404


def test_info_refs_proxies_with_injected_app_token(client, git_auth, upstream):
    upstream["ctype"] = "application/x-git-upload-pack-advertisement"
    resp = client.get(
        f"{GIT_PATH}/info/refs?service=git-upload-pack",
        headers=_basic(),
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith(
        "application/x-git-upload-pack-advertisement"
    )
    call = upstream["calls"][0]
    assert "acme/webshop.git/info/refs" in call["url"]
    assert "service=git-upload-pack" in call["url"]
    auth = call["headers"].get("Authorization", "")
    assert base64.b64decode(auth.split(" ", 1)[1]).startswith(b"x-access-token:")
    # the worker's own token never goes upstream
    assert "sfw_testtoken" not in str(call["headers"])


def test_fetch_outside_allow_list_is_404_but_push_handshake_is_not(
    client, git_auth, upstream, monkeypatch
):
    """US-3.12: allow-list mode hides the project from clone/fetch like a
    cross-org project — while the push handshake stays open so a claim
    already held can be worked to completion."""
    monkeypatch.setattr(
        "app.routers.gitproxy.db.worker_allowed_for_project",
        lambda s, w, p: False,
    )
    resp = client.get(
        f"{GIT_PATH}/info/refs?service=git-upload-pack", headers=_basic()
    )
    assert resp.status_code == 404
    resp = client.post(
        f"{GIT_PATH}/git-upload-pack",
        headers=_basic() | {"Content-Type": "application/x-git-upload-pack-request"},
        content=b"0032want deadbeef\n0000",
    )
    assert resp.status_code == 404
    # push handshake is claim-gated, not capability-gated
    resp = client.get(
        f"{GIT_PATH}/info/refs?service=git-receive-pack", headers=_basic()
    )
    assert resp.status_code == 200


def test_upload_pack_streams_body_through(client, git_auth, upstream):
    resp = client.post(
        f"{GIT_PATH}/git-upload-pack",
        headers=_basic() | {"Content-Type": "application/x-git-upload-pack-request"},
        content=b"0032want deadbeef\n0000",
    )
    assert resp.status_code == 200
    assert upstream["calls"][0]["body"] == b"0032want deadbeef\n0000"


def _patch_claim(monkeypatch, run=None):
    # US-7.3: the proxy matches a push to the run's stored branch_ref first,
    # then falls back to the legacy factory/issue-<id> lookup.
    monkeypatch.setattr(
        "app.routers.gitproxy.db.get_running_run_for_branch_ref",
        lambda s, project_id, branch, worker_id: run,
    )
    monkeypatch.setattr(
        "app.routers.gitproxy.db.get_claimed_run_for_branch",
        lambda s, project_id, issue_id, worker_id: run,
    )
    recorded = {}

    def fake_record(settings, run_id, head_sha, worker_name):
        recorded.update(run_id=run_id, head_sha=head_sha, worker=worker_name)

    monkeypatch.setattr("app.routers.gitproxy.db.record_branch_push", fake_record)
    return recorded


def test_push_to_unmatched_branch_refused(client, git_auth, upstream, monkeypatch):
    # US-7.3: a push to a branch with no matching claimed run is refused —
    # branches are matched by the run's stored branch_ref, not the name.
    _patch_claim(monkeypatch)
    resp = client.post(
        f"{GIT_PATH}/git-receive-pack",
        headers=_basic() | {"Content-Type": "application/x-git-receive-pack-request"},
        content=push_body(ZEROS, SHA_A, "refs/heads/main"),
    )
    assert resp.status_code == 200
    assert b"push rejected" in resp.content
    assert b"claim" in resp.content.lower()
    assert upstream["calls"] == []  # nothing reached GitHub


def test_push_to_non_branch_ref_refused(client, git_auth, upstream, monkeypatch):
    _patch_claim(monkeypatch, run={"id": RUN_ID, "pushed_head_sha": None})
    resp = client.post(
        f"{GIT_PATH}/git-receive-pack",
        headers=_basic() | {"Content-Type": "application/x-git-receive-pack-request"},
        content=push_body(ZEROS, SHA_A, "refs/tags/v1"),
    )
    assert resp.status_code == 200
    assert b"push rejected" in resp.content
    assert upstream["calls"] == []


def test_push_deletion_refused(client, git_auth, upstream, monkeypatch):
    _patch_claim(
        monkeypatch, run={"id": RUN_ID, "pushed_head_sha": SHA_A}
    )
    resp = client.post(
        f"{GIT_PATH}/git-receive-pack",
        headers=_basic() | {"Content-Type": "application/x-git-receive-pack-request"},
        content=push_body(SHA_A, ZEROS, f"refs/heads/factory/issue-{ISSUE_ID}"),
    )
    assert b"push rejected" in resp.content
    assert upstream["calls"] == []


def test_push_without_claim_refused(client, git_auth, upstream, monkeypatch):
    _patch_claim(monkeypatch, run=None)
    resp = client.post(
        f"{GIT_PATH}/git-receive-pack",
        headers=_basic() | {"Content-Type": "application/x-git-receive-pack-request"},
        content=push_body(ZEROS, SHA_A, f"refs/heads/factory/issue-{ISSUE_ID}"),
    )
    assert b"push rejected" in resp.content
    assert b"claim" in resp.content.lower()
    assert upstream["calls"] == []


def test_push_history_rewrite_refused(client, git_auth, upstream, monkeypatch):
    _patch_claim(
        monkeypatch, run={"id": RUN_ID, "pushed_head_sha": SHA_A}
    )
    resp = client.post(
        f"{GIT_PATH}/git-receive-pack",
        headers=_basic() | {"Content-Type": "application/x-git-receive-pack-request"},
        content=push_body(SHA_B, "c" * 40, f"refs/heads/factory/issue-{ISSUE_ID}"),
    )
    assert b"push rejected" in resp.content
    assert upstream["calls"] == []


def test_push_happy_path_forwards_and_records(client, git_auth, upstream, monkeypatch):
    recorded = _patch_claim(
        monkeypatch, run={"id": RUN_ID, "pushed_head_sha": None}
    )
    upstream["response"] = pkt(b"unpack ok\n") + pkt(
        f"ok refs/heads/factory/issue-{ISSUE_ID}\n".encode()
    ) + FLUSH
    body = push_body(ZEROS, SHA_A, f"refs/heads/factory/issue-{ISSUE_ID}")
    resp = client.post(
        f"{GIT_PATH}/git-receive-pack",
        headers=_basic() | {"Content-Type": "application/x-git-receive-pack-request"},
        content=body,
    )
    assert resp.status_code == 200
    assert b"unpack ok" in resp.content
    # the full request body — commands and packfile — reached upstream
    assert upstream["calls"][0]["body"] == body
    assert recorded["head_sha"] == SHA_A
    assert recorded["run_id"] == RUN_ID
    assert recorded["worker"] == WORKER["name"]


def test_push_not_recorded_when_upstream_rejects(
    client, git_auth, upstream, monkeypatch
):
    recorded = _patch_claim(
        monkeypatch, run={"id": RUN_ID, "pushed_head_sha": None}
    )
    upstream["response"] = pkt(b"unpack ok\n") + pkt(
        f"ng refs/heads/factory/issue-{ISSUE_ID} non-fast-forward\n".encode()
    ) + FLUSH
    resp = client.post(
        f"{GIT_PATH}/git-receive-pack",
        headers=_basic() | {"Content-Type": "application/x-git-receive-pack-request"},
        content=push_body(ZEROS, SHA_A, f"refs/heads/factory/issue-{ISSUE_ID}"),
    )
    assert resp.status_code == 200
    assert recorded == {}  # rejected pushes leave no push log


# --- US-9.19: Power Git access ------------------------------------------------

PRINCIPAL_ID = str(uuid.uuid4())

FULL_GRANT = {
    "allow_default_branch": True,
    "allow_force_push": True,
    "allow_branch_delete": True,
    "allow_tag_push": True,
    "default_branch": "main",
}


def _patch_power(monkeypatch, grant, recorded_head=None):
    """Authenticate as a principal-bearing worker with a Power Git grant, and
    prove the claim path is NOT what admits the push (both claim lookups None)."""
    powered = dict(WORKER, principal_id=PRINCIPAL_ID)
    monkeypatch.setattr(
        "app.routers.gitproxy.db.get_worker_by_token",
        lambda s, t: dict(powered) if t == "sfw_testtoken" else None,
    )
    monkeypatch.setattr(
        "app.routers.gitproxy.db.get_git_power_grant",
        lambda s, project_id, principal_id: dict(grant) if grant else None,
    )
    monkeypatch.setattr(
        "app.routers.gitproxy.db.get_git_power_branch_head",
        lambda s, project_id, principal_id, branch: recorded_head,
    )
    monkeypatch.setattr(
        "app.routers.gitproxy.db.get_running_run_for_branch_ref",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "app.routers.gitproxy.db.get_claimed_run_for_branch",
        lambda *a, **k: None,
    )
    rec = {}

    def fake_record(s, project_id, principal_id, branch, head_sha):
        rec.update(
            project_id=project_id,
            principal_id=principal_id,
            branch=branch,
            head_sha=head_sha,
        )

    monkeypatch.setattr(
        "app.routers.gitproxy.db.record_git_power_branch_head", fake_record
    )
    return rec


def _receive(client, body):
    return client.post(
        f"{GIT_PATH}/git-receive-pack",
        headers=_basic() | {"Content-Type": "application/x-git-receive-pack-request"},
        content=body,
    )


def test_power_push_bypasses_claim_and_lands(client, git_auth, upstream, monkeypatch):
    """A granted principal pushes a branch tied to NO claimed run — it lands on
    GitHub and its head is recorded."""
    rec = _patch_power(monkeypatch, FULL_GRANT)
    upstream["response"] = pkt(b"unpack ok\n") + FLUSH
    resp = _receive(client, push_body(ZEROS, SHA_A, "refs/heads/my-feature"))
    assert resp.status_code == 200
    assert b"unpack ok" in resp.content
    assert len(upstream["calls"]) == 1  # reached GitHub despite no claim
    assert rec == {
        "project_id": PROJECT_ID,
        "principal_id": PRINCIPAL_ID,
        "branch": "my-feature",
        "head_sha": SHA_A,
    }


def test_power_default_branch_rail(client, git_auth, upstream, monkeypatch):
    # rail on: direct push to the default branch is refused
    _patch_power(monkeypatch, dict(FULL_GRANT, allow_default_branch=False))
    resp = _receive(client, push_body(SHA_A, SHA_B, "refs/heads/main"))
    assert b"push rejected" in resp.content
    assert b"default branch" in resp.content.lower()
    assert upstream["calls"] == []

    # rail off (default): the same push lands
    _patch_power(monkeypatch, FULL_GRANT)
    upstream["response"] = pkt(b"unpack ok\n") + FLUSH
    resp = _receive(client, push_body(SHA_A, SHA_B, "refs/heads/main"))
    assert resp.status_code == 200
    assert len(upstream["calls"]) == 1


def test_power_branch_delete_rail(client, git_auth, upstream, monkeypatch):
    _patch_power(monkeypatch, dict(FULL_GRANT, allow_branch_delete=False))
    resp = _receive(client, push_body(SHA_A, ZEROS, "refs/heads/my-feature"))
    assert b"push rejected" in resp.content
    assert b"deletion" in resp.content.lower()
    assert upstream["calls"] == []


def test_power_tag_rail(client, git_auth, upstream, monkeypatch):
    # rail on: tag push refused
    _patch_power(monkeypatch, dict(FULL_GRANT, allow_tag_push=False))
    resp = _receive(client, push_body(ZEROS, SHA_A, "refs/tags/v1"))
    assert b"push rejected" in resp.content
    assert upstream["calls"] == []

    # rail off: tag push lands (and is not recorded as a branch head)
    rec = _patch_power(monkeypatch, FULL_GRANT)
    upstream["response"] = pkt(b"unpack ok\n") + FLUSH
    resp = _receive(client, push_body(ZEROS, SHA_A, "refs/tags/v1"))
    assert resp.status_code == 200
    assert len(upstream["calls"]) == 1
    assert rec == {}  # a tag is not a branch head


def test_power_force_push_rail(client, git_auth, upstream, monkeypatch):
    # rail on + a recorded head that the pushed old doesn't match → rewrite refused
    _patch_power(
        monkeypatch, dict(FULL_GRANT, allow_force_push=False), recorded_head=SHA_A
    )
    resp = _receive(client, push_body(SHA_B, "c" * 40, "refs/heads/my-feature"))
    assert b"push rejected" in resp.content
    assert b"force-push" in resp.content.lower()
    assert upstream["calls"] == []

    # rail on but old matches the recorded head → fast-forward lands
    _patch_power(
        monkeypatch, dict(FULL_GRANT, allow_force_push=False), recorded_head=SHA_A
    )
    upstream["response"] = pkt(b"unpack ok\n") + FLUSH
    resp = _receive(client, push_body(SHA_A, SHA_B, "refs/heads/my-feature"))
    assert resp.status_code == 200
    assert len(upstream["calls"]) == 1


# --------------------------------------------------------------------------
# Credential refresh: a cached token GitHub refuses must not be replayed for
# the rest of its TTL — a manager who reconnects GitHub gets a working remote
# on the next fetch, not up to 50 minutes later.
# --------------------------------------------------------------------------


CACHE_KEY = f"{ORG_ID}:acme/webshop"


@pytest.fixture
def token_cache(monkeypatch):
    """Exercise the real _repo_token (git_auth stubs it out) over a fake
    org-credential resolver, with the module cache isolated per test."""
    monkeypatch.setattr("app.routers.gitproxy._repo_token", REAL_REPO_TOKEN)
    gitproxy._token_cache.clear()
    state = {"minted": [], "next_token": "ghs_fresh"}

    async def fake_token_for_org(settings, org_id, repo_full_name=None):
        state["minted"].append(org_id)
        return state["next_token"]

    monkeypatch.setattr(
        "app.routers.gitproxy.github_tokens.token_for_org", fake_token_for_org
    )
    yield state
    gitproxy._token_cache.clear()


@pytest.fixture
def refusing_upstream(monkeypatch):
    """Upstream that 401s any credential not in `accepts`."""
    captured = {"calls": [], "accepts": set()}

    async def fake_upstream(method, url, headers, content=None):
        if content is not None:
            async for _ in content:
                pass
        auth = headers.get("Authorization", "")
        token = base64.b64decode(auth.split(" ", 1)[1]).decode().split(":", 1)[1]
        captured["calls"].append({"url": url, "token": token})
        ok = token in captured["accepts"]

        async def it():
            yield b"0008ok\n0000" if ok else b"Invalid username or token.\n"

        return (200 if ok else 401, {"content-type": "text/plain"}, it())

    monkeypatch.setattr("app.routers.gitproxy._upstream_stream", fake_upstream)
    return captured


def test_stale_cached_credential_is_evicted_and_retried(
    client, git_auth, token_cache, refusing_upstream
):
    import time as _time

    # a credential cached before the manager reconnected — still inside its TTL
    gitproxy._token_cache[CACHE_KEY] = ("ghs_dead", _time.time() + 3000)
    refusing_upstream["accepts"] = {"ghs_fresh"}

    resp = client.get(
        f"{GIT_PATH}/info/refs?service=git-upload-pack", headers=_basic()
    )

    assert resp.status_code == 200
    tokens = [c["token"] for c in refusing_upstream["calls"]]
    assert tokens == ["ghs_dead", "ghs_fresh"]  # refused once, then re-resolved
    # the working credential replaces the dead one for subsequent fetches
    assert gitproxy._token_cache[CACHE_KEY][0] == "ghs_fresh"


def test_persistent_refusal_answers_readable_403(
    client, git_auth, token_cache, refusing_upstream
):
    token_cache["next_token"] = "ghs_also_dead"
    refusing_upstream["accepts"] = set()

    resp = client.get(
        f"{GIT_PATH}/info/refs?service=git-upload-pack", headers=_basic()
    )

    # 403, not GitHub's 401 — git stops instead of re-prompting for a password
    assert resp.status_code == 403
    assert resp.headers["content-type"].startswith("text/plain")
    body = resp.content.decode()
    assert "reconnect GitHub" in body
    assert "username" not in body.lower()  # GitHub's misleading advice is gone
    assert len(refusing_upstream["calls"]) == 2  # tried once more, then gave up
    assert CACHE_KEY not in gitproxy._token_cache  # nothing dead left cached
