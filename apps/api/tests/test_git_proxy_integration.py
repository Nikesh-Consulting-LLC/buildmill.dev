"""US-3.8: real-git-client integration — clone → branch → commit → push
through the factory proxy, against a local `git http-backend` stub
standing in for GitHub. Requires git and DATABASE_URL; skips otherwise.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import socket
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

GIT = shutil.which("git")

pytestmark = pytest.mark.skipif(GIT is None, reason="git not installed")


# US-80.1: spins a real server and pushes through it, resolving the project in Postgres on the way, so this is Full QA (--full).
pytestmark = pytest.mark.needs_db

def _database_url() -> str | None:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    env = Path(__file__).resolve().parents[1] / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip().strip('"')
    return None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run_git(*args, cwd=None, env_extra=None):
    env = os.environ.copy()
    env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        }
    )
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [GIT, *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=60
    )


class _GitCGIHandler(BaseHTTPRequestHandler):
    """Minimal smart-HTTP server over `git http-backend` (the GitHub stub)."""

    project_root: str = ""

    def _read_body(self) -> bytes:
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            body = b""
            while True:
                size_line = self.rfile.readline().strip()
                size = int(size_line, 16)
                if size == 0:
                    self.rfile.readline()
                    return body
                body += self.rfile.read(size)
                self.rfile.readline()  # trailing CRLF
        length = int(self.headers.get("Content-Length", 0) or 0)
        return self.rfile.read(length) if length else b""

    def _cgi(self):
        path, _, query = self.path.partition("?")
        env = os.environ.copy()
        env.update(
            {
                "GIT_PROJECT_ROOT": self.project_root,
                "GIT_HTTP_EXPORT_ALL": "1",
                "REQUEST_METHOD": self.command,
                "PATH_INFO": path,
                "QUERY_STRING": query,
                "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                "REMOTE_USER": "factory",
                "REMOTE_ADDR": "127.0.0.1",
                "GATEWAY_INTERFACE": "CGI/1.1",
            }
        )
        if self.headers.get("Content-Encoding"):
            env["HTTP_CONTENT_ENCODING"] = self.headers["Content-Encoding"]
        body = self._read_body()
        env["CONTENT_LENGTH"] = str(len(body))

        proc = subprocess.run(
            [GIT, "http-backend"],
            input=body,
            env=env,
            capture_output=True,
            timeout=60,
        )
        raw = proc.stdout
        header_end = raw.find(b"\r\n\r\n")
        headers, payload = raw[:header_end], raw[header_end + 4 :]
        status = 200
        out_headers = []
        for line in headers.split(b"\r\n"):
            name, _, value = line.decode().partition(":")
            if name.lower() == "status":
                status = int(value.strip().split(" ")[0])
            elif name:
                out_headers.append((name, value.strip()))
        self.send_response(status)
        for name, value in out_headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = _cgi
    do_POST = _cgi

    def log_message(self, *args):  # silence
        pass


@pytest.fixture(scope="module")
def db():
    url = _database_url()
    if not url:
        pytest.skip("DATABASE_URL not set")
    try:
        conn = psycopg.connect(url, row_factory=dict_row)
        conn.execute("select 1")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"database unreachable: {e}")
    yield conn
    conn.close()


@pytest.fixture()
def stack(db, tmp_path, monkeypatch):
    """Fake GitHub (git http-backend) + the API under uvicorn + DB rows:
    a worker, an issue, and a run claimed by that worker."""
    import uvicorn

    from app.config import Settings, get_settings
    from app.main import app

    project = db.execute(
        "select p.id, p.org_id, p.repo_full_name, p.slug, o.shortname "
        "from public.projects p "
        "join public.organizations o on o.id = p.org_id "
        "order by p.created_at limit 1"
    ).fetchone()
    if not project:
        pytest.skip("no project")

    # --- fake GitHub: bare repo at <root>/<repo_full_name>.git
    root = tmp_path / "github"
    bare = root / f"{project['repo_full_name']}.git"
    bare.parent.mkdir(parents=True)
    assert _run_git("init", "--bare", str(bare)).returncode == 0
    assert (
        _run_git("config", "http.receivepack", "true", cwd=str(bare)).returncode == 0
    )
    _GitCGIHandler.project_root = str(root)
    gh_port = _free_port()
    gh_server = ThreadingHTTPServer(("127.0.0.1", gh_port), _GitCGIHandler)
    threading.Thread(target=gh_server.serve_forever, daemon=True).start()

    # --- API under uvicorn, upstream pointed at the stub
    real = Settings()
    patched = real.model_copy(
        update={"git_upstream_base": f"http://127.0.0.1:{gh_port}"}
    )
    app.dependency_overrides[get_settings] = lambda: patched

    async def fake_repo_token(settings, org_id, repo_full_name):
        return "stub-token"

    monkeypatch.setattr("app.routers.gitproxy._repo_token", fake_repo_token)

    api_port = _free_port()
    config = uvicorn.Config(
        app, host="127.0.0.1", port=api_port, log_level="warning"
    )
    server = uvicorn.Server(config)
    api_thread = threading.Thread(target=server.run, daemon=True)
    api_thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)

    # --- DB rows: worker + claimed run
    token = f"sfw_it_{uuid.uuid4().hex}"
    worker_id = db.execute(
        """
        insert into public.workers (org_id, name, type, token_hash, token_last4)
        values (%s, 'git-it-worker', 'autonomous', %s, %s) returning id
        """,
        (
            project["org_id"],
            hashlib.sha256(token.encode()).hexdigest(),
            token[-4:],
        ),
    ).fetchone()["id"]
    # US-31.3: the git-proxy read gate is fail-CLOSED now — a worker with zero
    # capability rows can clone nothing. That inversion is the point of
    # us-31.3 and is tested there; this suite is about the proxy's own
    # clone/commit/push and branch-policy behaviour, so grant the fixture
    # worker the project it is meant to be working on.
    for capability in ("plan", "code"):
        db.execute(
            """
            insert into public.worker_capabilities
              (org_id, worker_id, project_id, capability)
            values (%s, %s, %s, %s)
            on conflict do nothing
            """,
            (project["org_id"], worker_id, project["id"], capability),
        )
    issue_id, run_id = uuid.uuid4(), uuid.uuid4()
    db.execute(
        """
        insert into public.issues
          (id, org_id, project_id, type, title, body, acceptance_criteria, status)
        values (%s, %s, %s, 'story', %s, 'b', '["ok"]'::jsonb, 'running')
        """,
        (issue_id, project["org_id"], project["id"], f"git-it {issue_id}"),
    )
    db.execute(
        """
        insert into public.runs
          (id, org_id, issue_id, provider, status, kind, input_context,
           worker_id, claimed_at, claim_expires_at)
        values (%s, %s, %s, 'claude', 'running', 'code', '{}'::jsonb,
                %s, now(), now() + interval '15 minutes')
        """,
        (run_id, project["org_id"], issue_id, worker_id),
    )
    db.commit()

    yield {
        "remote": (
            f"http://worker:{token}@127.0.0.1:{api_port}"
            f"/git/{project['shortname']}/{project['slug']}.git"
        ),
        "issue_id": str(issue_id),
        "run_id": str(run_id),
        "workdir": tmp_path / "clone",
    }

    server.should_exit = True
    api_thread.join(timeout=10)
    gh_server.shutdown()
    app.dependency_overrides.pop(get_settings, None)
    db.rollback()
    db.execute(
        "update public.issues set status = 'failed' where id = %s", (issue_id,)
    )
    db.execute("delete from public.issues where id = %s", (issue_id,))
    db.execute("delete from public.workers where id = %s", (worker_id,))
    db.commit()


def test_clone_commit_push_through_factory_remote(db, stack):
    clone = stack["workdir"]
    res = _run_git("clone", stack["remote"], str(clone))
    assert res.returncode == 0, res.stderr

    branch = f"factory/issue-{stack['issue_id']}"
    assert _run_git("checkout", "-b", branch, cwd=str(clone)).returncode == 0
    (clone / "hello.txt").write_text("factory\n")
    assert _run_git("add", ".", cwd=str(clone)).returncode == 0
    assert _run_git("commit", "-m", "factory change", cwd=str(clone)).returncode == 0

    res = _run_git("push", "origin", branch, cwd=str(clone))
    assert res.returncode == 0, res.stderr

    head = _run_git("rev-parse", "HEAD", cwd=str(clone)).stdout.strip()
    run = db.execute(
        "select pushed_head_sha, pushed_at from public.runs where id = %s",
        (stack["run_id"],),
    ).fetchone()
    assert run["pushed_head_sha"] == head
    assert run["pushed_at"] is not None

    ev = db.execute(
        """
        select payload from public.issue_events
        where issue_id = %s and type = 'branch-pushed'
        """,
        (stack["issue_id"],),
    ).fetchone()
    assert ev["payload"]["head_sha"] == head
    assert ev["payload"]["worker"] == "git-it-worker"


def test_push_to_disallowed_branch_refused(db, stack):
    clone = stack["workdir"] / "../clone2"
    res = _run_git("clone", stack["remote"], str(clone))
    assert res.returncode == 0, res.stderr

    assert _run_git("checkout", "-b", "sneaky", cwd=str(clone)).returncode == 0
    (Path(str(clone)) / "x.txt").write_text("x\n")
    _run_git("add", ".", cwd=str(clone))
    _run_git("commit", "-m", "x", cwd=str(clone))

    res = _run_git("push", "origin", "sneaky", cwd=str(clone))
    assert res.returncode != 0
    # US-7.3: a push is now matched to the run's stored branch_ref; a branch
    # with no matching claimed run is refused (message names the claim).
    assert "push rejected" in (res.stderr + res.stdout)
    assert "claim" in (res.stderr + res.stdout).lower()
