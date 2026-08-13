"""US-50.5: a branch-source deployment run reaches the end of the pipeline.

`deploy.py` looked up `state` as a free variable inside `_pipeline_inner`,
where it does not exist — so every run whose payload came from a branch died
with `NameError: name 'state' is not defined` the moment the branch head was
resolved, and `run_pipeline`'s catch-all turned that into an ordinary failed
run. Zip and archived-payload runs skipped the line, which is why the pipeline
looked partly healthy for six weeks.

A signature assertion would not have caught it, and neither would a test that
mocked the function under test: the bug is a name that only fails when the
line is actually executed, on one of four payload paths. So these drive the
REAL `_pipeline_inner` with the SSH, SFTP and GitHub edges faked, and no
database.
"""

from __future__ import annotations

import asyncio

import pytest

from app import deploy
from app.config import Settings

BRANCH_SHA = "abc1234deadbeefcafe00001111222233334444"


@pytest.fixture()
def settings():
    return Settings(
        supabase_url="https://test.supabase.co",
        supabase_publishable_key="sb_publishable_test",
        cors_origins="http://localhost:3000",
        database_url="postgresql://test",
    )


DEPLOYMENT = {
    "id": "dep-1",
    "org_id": "org-1",
    "project_id": "proj-1",
    "name": "staging",
    "branch": "main",
    "target_folder": "/var/www/app",
    "script": "",  # transfer-only: no script, no env vars to resolve
    "strategy": "in-place",  # no release flip, no retention prune
    "keep_releases": 5,
    "source_folder": "",
    "exclude_patterns": "",
    "health_check_url": "",  # no verify step
    "run_timeout_minutes": 30,
}

SERVER = {
    "id": "srv-1",
    "org_id": "org-1",
    "host": "box.example.com",
    "port": 22,
    "username": "deploy",
    "auth_method": "password",
    "host_key_fingerprint": "SHA256:abc",
}


class _FakeConn:
    transport = object()

    def close(self):
        pass


async def _noop_event(phase, message, data=None):
    return None


class _Rig:
    """Every edge the pipeline touches, faked; everything it decides, real."""

    def __init__(self):
        self.updates: dict = {}
        self.events: list[tuple[str, str]] = []
        self.notifications: list[dict] = []
        self.uploaded: list[tuple[str, str]] = []
        self.commands: list[str] = []
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
            lambda s, org, run, phase, message, data=None: self.events.append(
                (phase, message)
            ),
        )
        monkeypatch.setattr(deploy, "_set_current_run", lambda s, d, r: None)
        monkeypatch.setattr(
            deploy.notify,
            "notify_deployment_event",
            lambda s, **kw: self.notifications.append(kw),
        )

        # --- the SSH/SFTP edge ---
        async def fake_connect(settings, server):
            return _FakeConn()

        monkeypatch.setattr(deploy, "connect_to_server", fake_connect)
        monkeypatch.setattr(
            deploy,
            "preflight_checks",
            lambda transport, target, min_free_mb=200, tools=("tar",),
            space_reason=None: [
                {"check": "ssh", "ok": True, "detail": "Connected"},
                {"check": "disk-space", "ok": True, "detail": "plenty"},
            ],
        )
        monkeypatch.setattr(
            deploy,
            "_upload",
            lambda transport, local, remote, cb: self.uploaded.append((local, remote)),
        )

        def fake_exec(transport, command, stdin=None, line_cb=None):
            self.commands.append(command)
            return 0

        monkeypatch.setattr(deploy, "_exec", fake_exec)

        # --- the GitHub edge ---
        async def fake_token(settings, org_id, repo_full_name=None):
            return "gh-token"

        async def fake_branch(token, owner, repo, branch):
            return {
                "commit": {
                    "sha": BRANCH_SHA,
                    "commit": {"message": "ship the CSV export\n\nbody"},
                }
            }

        async def fake_tarball(token, owner, repo, ref, dest_path):
            with open(dest_path, "wb") as f:
                f.write(b"tarball-bytes")
            return 13

        monkeypatch.setattr("app.github_tokens.token_for_org", fake_token)
        monkeypatch.setattr("app.github.get_branch", fake_branch)
        monkeypatch.setattr("app.github.download_tarball", fake_tarball)

        # --- the archive step's bucket write ---
        async def fake_put(settings, path, data, content_type=None):
            return None

        monkeypatch.setattr(deploy.storage, "put_object", fake_put)
        return self


def _ctx(**over):
    return {
        "run_id": "run-1",
        "org_id": "org-1",
        "deployment": dict(DEPLOYMENT),
        "server": dict(SERVER),
        "repo_full_name": "acme/site",
        "project_name": "Site",
        "triggered_by": "manager@example.com",
        **over,
    }


def test_a_branch_run_succeeds_instead_of_a_nameerror(settings, monkeypatch):
    rig = _Rig().install(monkeypatch)

    asyncio.run(deploy.run_pipeline(settings, _ctx()))

    assert rig.updates["status"] == "succeeded", rig.log
    # The exact symptom this story exists for.
    assert "not defined" not in rig.log
    assert rig.updates["commit_sha"] == BRANCH_SHA
    assert rig.updates["commit_message"] == "ship the CSV export"
    # It really walked the pipeline rather than short-circuiting.
    assert rig.uploaded, "the payload was never uploaded"
    assert any("tar -xzf" in c for c in rig.commands)


def test_the_notification_names_the_commit_that_shipped(settings, monkeypatch):
    """US-2.16's whole point, and the reason the broken line was added."""
    rig = _Rig().install(monkeypatch)

    asyncio.run(deploy.run_pipeline(settings, _ctx()))

    succeeded = [n for n in rig.notifications if n["event"] == "succeeded"]
    assert succeeded, rig.notifications
    assert succeeded[0]["source"] == f"branch main @ {BRANCH_SHA[:7]}"
    # The 'started' event fires before the head is resolved, so it stays
    # branch-only — that ordering is deliberate, not a miss.
    started = [n for n in rig.notifications if n["event"] == "started"]
    assert started[0]["source"] == "branch main"


def test_a_ref_override_records_its_sha_too(settings, monkeypatch):
    """The assignment sits below the override/head branch, so US-1.50's
    one-off ref reaches it on the same line."""
    rig = _Rig().install(monkeypatch)
    override = {"ref": "feat-x", "sha": "0ff5e7c" + "0" * 33, "message": "wip"}

    asyncio.run(deploy.run_pipeline(settings, _ctx(override=override)))

    assert rig.updates["status"] == "succeeded", rig.log
    assert rig.updates["commit_sha"] == override["sha"]
    succeeded = [n for n in rig.notifications if n["event"] == "succeeded"]
    assert succeeded[0]["source"] == f"branch main @ {override['sha'][:7]}"


def test_a_zip_run_is_unchanged(settings, monkeypatch):
    """Zip payloads skip the GitHub branch entirely — they were never broken,
    and their notification still names the zip rather than a commit."""
    rig = _Rig().install(monkeypatch)

    async def fake_get(settings, path):
        return b"PK\x03\x04staged-zip-bytes"

    monkeypatch.setattr(deploy.storage, "get_object", fake_get)

    asyncio.run(
        deploy.run_pipeline(
            settings, _ctx(source="zip", zip_filename="build.zip")
        )
    )

    assert rig.updates["status"] == "succeeded", rig.log
    assert "commit_sha" not in rig.updates  # no commit identity for a zip
    succeeded = [n for n in rig.notifications if n["event"] == "succeeded"]
    assert succeeded[0]["source"] == "zip build.zip"
    assert any("unzip -o" in c for c in rig.commands)


def test_pipeline_inner_is_handed_the_state_it_writes_into(settings, monkeypatch):
    """The narrow guard: call `_pipeline_inner` directly and confirm the
    caller's dict is what comes back carrying the SHA. A closure would have
    made this assertion impossible to write."""
    rig = _Rig().install(monkeypatch)
    state: dict = {"log": [], "unflushed": 0}

    asyncio.run(
        deploy._pipeline_inner(
            settings,
            _ctx(),
            state,
            lambda line: state["log"].append(line),
            lambda: None,
            _noop_event,
        )
    )

    assert state["commit_sha"] == BRANCH_SHA
