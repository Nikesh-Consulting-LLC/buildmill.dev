"""US-89.1: no credential ever rides a URL.

The worker token used to be embedded as HTTP Basic in the factory remote —
which put it in `.git/config` on disk and verbatim into every audit row.
Auth now rides a per-repo credential helper that reads FACTORY_WORKER_TOKEN
from the process environment at fetch/push time.
"""

import asyncio

from supervisor import gitwork
from supervisor.modules.base import ShellResult


class FakePrim:
    def __init__(self):
        self.calls: list[list[str]] = []

    async def run_shell(self, argv, cwd=None, timeout=None, on_line=None):
        self.calls.append([str(a) for a in argv])
        return ShellResult(argv=argv, exit_code=0, stdout="")


def test_clean_url_strips_userinfo_and_passes_other_schemes():
    assert (
        gitwork.clean_url("https://worker:sfw_secret@api.example.test/git/a/b.git")
        == "https://api.example.test/git/a/b.git"
    )
    assert (
        gitwork.clean_url("https://api.example.test/git/a/b.git")
        == "https://api.example.test/git/a/b.git"
    )
    assert gitwork.clean_url("file:///tmp/repo") == "file:///tmp/repo"


def test_fresh_clone_uses_the_helper_and_a_clean_url(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNNER_WORKSPACE", str(tmp_path))
    prim = FakePrim()
    asyncio.run(
        gitwork.prepare_checkout(
            prim,
            "https://api.example.test/git/acme/demo.git",
            "abc12345",
            project_id="abcd1234-0000-0000-0000-000000000000",
        )
    )
    clone = next(c for c in prim.calls if "clone" in c)
    assert f"credential.helper={gitwork.CRED_HELPER}" in clone
    assert "https://api.example.test/git/acme/demo.git" in clone
    assert not any("worker:" in part for c in prim.calls for part in c)


def test_existing_repo_gets_helper_and_a_scrubbed_remote(tmp_path, monkeypatch):
    """A pre-89.1 workspace whose remote still embeds a token is repaired."""
    monkeypatch.setenv("RUNNER_WORKSPACE", str(tmp_path))
    workdir = tmp_path / "project-abcd1234"
    (workdir / ".git").mkdir(parents=True)
    prim = FakePrim()
    asyncio.run(
        gitwork.prepare_checkout(
            prim,
            "https://worker:sfw_leaked@api.example.test/git/acme/demo.git",
            "abc12345",
            project_id="abcd1234-0000-0000-0000-000000000000",
        )
    )
    set_url = next(c for c in prim.calls if "set-url" in c)
    assert set_url[-1] == "https://api.example.test/git/acme/demo.git"
    config = next(c for c in prim.calls if c[1] == "config")
    assert config[-1] == gitwork.CRED_HELPER
    assert not any("sfw_leaked" in part for c in prim.calls for part in c)
