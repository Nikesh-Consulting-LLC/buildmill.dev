"""US-1.52: POST /projects/{id}/guidelines/save-instructions.

US-22.6 rewrote what this endpoint does. It used to replace AGENTS.md
wholesale and stamp CLAUDE.md with a bare pointer, which destroyed the
docs-tree section and any hand-written prose every time it was pressed. It
now writes one marker-fenced block through the same path the docs sync
uses, in a single commit.
"""

import uuid

from app import repo_docs
from app import github_tokens as github_tokens_module
from app import github as github_module

PROJECT_ID = str(uuid.uuid4())

_SAMPLE_PROJECT = {
    "repo_full_name": "acme/demo",
    "default_branch": "main",
    "docs_tree_enabled": True,
    # US-76.4: the credential is resolved from the project's own org, so the
    # row has to carry it.
    "org_id": "org-1",
}


def _patch_deps(monkeypatch, *, project=None, guidelines="## Stack\n\nNode."):
    async def fake_get(settings, token, path, params):
        if path == "projects":
            if project is None:
                return []
            return [project]
        raise AssertionError(f"unexpected path {path}")

    async def fake_rpc(settings, token, fn, args):
        assert fn == "assemble_project_guidelines"
        assert args == {"p_project": PROJECT_ID}
        return guidelines

    monkeypatch.setattr("app.routers.projects.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.projects.rpc", fake_rpc)
    monkeypatch.setattr(
        "app.routers.projects.db.record_instructions_sync",
        lambda *a, **k: None,
    )


def _patch_github(monkeypatch, *, token="gh-token", current=None):
    """Stub the two GitHub touchpoints the new path uses: reading the current
    instruction files, and one commit carrying both."""
    current = current or {"AGENTS.md": None, "CLAUDE.md": None}
    commits = []

    async def fake_token_for_user(settings, user_token, org_id, repo_full_name=None):
        assert repo_full_name == "acme/demo"
        return token

    async def fake_current(tok, owner, repo, branch):
        assert tok == token
        assert (owner, repo, branch) == ("acme", "demo", "main")
        return dict(current)

    async def fake_commit(tok, repo_full, branch, message, files, deletes=None):
        commits.append(
            {"files": files, "message": message, "deletes": deletes}
        )
        return {"commit_sha": "deadbee"}

    monkeypatch.setattr(github_tokens_module, "token_for_user", fake_token_for_user)
    monkeypatch.setattr(repo_docs, "_current_instruction_files", fake_current)
    monkeypatch.setattr(repo_docs, "commit_files", fake_commit)
    return commits


def _post(client, make_token=None, auth=True):
    headers = {"Authorization": f"Bearer {make_token()}"} if auth else {}
    return client.post(
        f"/api/v1/projects/{PROJECT_ID}/guidelines/save-instructions", headers=headers
    )


def test_save_instructions_requires_auth(client):
    resp = client.post(f"/api/v1/projects/{PROJECT_ID}/guidelines/save-instructions")
    assert resp.status_code == 401


def test_save_instructions_writes_both_files_in_one_commit(
    client, make_token, monkeypatch
):
    _patch_deps(
        monkeypatch, project=_SAMPLE_PROJECT, guidelines="## Stack\n\nNode + Supabase."
    )
    commits = _patch_github(monkeypatch)

    resp = _post(client, make_token)
    assert resp.status_code == 200
    body = resp.json()
    assert body["commit_sha"] == "deadbee"
    assert body["agents_md"]["html_url"].endswith("/AGENTS.md")
    assert body["claude_md"]["html_url"].endswith("/CLAUDE.md")

    # One commit — either both files land or neither does.
    assert len(commits) == 1
    files = commits[0]["files"]
    assert sorted(files) == ["AGENTS.md", "CLAUDE.md"]
    assert "Node + Supabase." in files["AGENTS.md"]
    assert repo_docs.BLOCK_START in files["AGENTS.md"]
    assert files["CLAUDE.md"] == repo_docs.CLAUDE_MD_POINTER


def test_save_instructions_preserves_hand_written_content(
    client, make_token, monkeypatch
):
    """The defect this story fixes: pressing the button used to erase
    everything the factory did not write."""
    _patch_deps(monkeypatch, project=_SAMPLE_PROJECT)
    commits = _patch_github(
        monkeypatch,
        current={
            "AGENTS.md": "# Our rules\n\nKEEP ME.\n",
            "CLAUDE.md": "# My CLAUDE.md\n\nKEEP ME TOO.\n",
        },
    )

    resp = _post(client, make_token)
    assert resp.status_code == 200
    files = commits[0]["files"]
    assert "KEEP ME." in files["AGENTS.md"]
    assert "KEEP ME TOO." in files["CLAUDE.md"]
    assert repo_docs.BLOCK_START in files["CLAUDE.md"]


def test_save_instructions_no_longer_overwrites_agents_md_wholesale(
    client, make_token, monkeypatch
):
    """us-22.6 acceptance: the wholesale-overwrite path is gone, not merely
    bypassed. create_or_update_file must not be reachable from here."""
    _patch_deps(monkeypatch, project=_SAMPLE_PROJECT)
    _patch_github(monkeypatch)

    async def must_not_be_called(*a, **k):
        raise AssertionError("save-instructions must not write whole files")

    monkeypatch.setattr(github_module, "create_or_update_file", must_not_be_called)
    assert _post(client, make_token).status_code == 200


def test_save_instructions_matches_what_a_sync_would_write(
    client, make_token, monkeypatch
):
    """Both writers, one block: pressing the button and approving a plan must
    produce byte-identical files."""
    guidelines = "## Stack\n\nNode + Supabase."
    _patch_deps(monkeypatch, project=_SAMPLE_PROJECT, guidelines=guidelines)
    commits = _patch_github(monkeypatch)
    assert _post(client, make_token).status_code == 200
    from_button = commits[0]["files"]

    # What sync_tree assembles for the same project state.
    block = repo_docs.build_instruction_block(guidelines, True)
    from_sync = repo_docs.instruction_files(block, None, None)

    assert from_button == from_sync


def test_save_instructions_project_not_found_is_404(client, make_token, monkeypatch):
    _patch_deps(monkeypatch, project=None)
    resp = _post(client, make_token)
    assert resp.status_code == 404


def test_save_instructions_empty_guidelines_is_400(client, make_token, monkeypatch):
    _patch_deps(monkeypatch, project=_SAMPLE_PROJECT, guidelines="   ")
    resp = _post(client, make_token)
    assert resp.status_code == 400


def test_save_instructions_no_linked_repo_is_409(client, make_token, monkeypatch):
    _patch_deps(
        monkeypatch, project={**_SAMPLE_PROJECT, "repo_full_name": ""}
    )
    assert _post(client, make_token).status_code == 409


def test_save_instructions_no_github_connection_is_409(client, make_token, monkeypatch):
    _patch_deps(monkeypatch, project=_SAMPLE_PROJECT)

    async def boom(settings, user_token, org_id, repo_full_name=None):
        raise github_module.GitHubError("no GitHub connection for this org")

    monkeypatch.setattr(github_tokens_module, "token_for_user", boom)

    resp = _post(client, make_token)
    assert resp.status_code == 409


def test_save_instructions_github_write_failure_is_502(client, make_token, monkeypatch):
    _patch_deps(monkeypatch, project=_SAMPLE_PROJECT)

    async def fake_token_for_user(settings, user_token, org_id, repo_full_name=None):
        return "gh-token"

    async def boom(*args, **kwargs):
        raise github_module.GitHubError("could not write AGENTS.md: 403")

    monkeypatch.setattr(github_tokens_module, "token_for_user", fake_token_for_user)
    monkeypatch.setattr(repo_docs, "_current_instruction_files", boom)

    resp = _post(client, make_token)
    assert resp.status_code == 502
