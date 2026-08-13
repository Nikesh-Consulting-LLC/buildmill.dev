"""Server registry, SSH terminal, file manager endpoints (US-1.28–1.30, 1.46).

The SSH/SFTP layer is mocked — these tests cover authorization, cross-org
isolation, credential write-only behaviour, and error mapping, not a live
SSH server.
"""

import json

import pytest

from app import sftp as sftp_ops
from app import ssh

AUTH = None  # set per-test via make_token


def _auth(make_token, **claims):
    return {"Authorization": f"Bearer {make_token(**claims)}"}


# --- auth required ---------------------------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/api/v1/servers"),
        ("PATCH", "/api/v1/servers/srv-1"),
        ("DELETE", "/api/v1/servers/srv-1"),
        ("POST", "/api/v1/servers/srv-1/test"),
        ("GET", "/api/v1/servers/srv-1/files"),
    ],
)
def test_requires_auth(client, method, path):
    resp = client.request(method, path, json={} if method != "GET" else None)
    assert resp.status_code == 401


# --- cross-org isolation: an unknown/foreign server id is a 404 ------------


def _server_not_visible(monkeypatch):
    async def fake_get(settings, token, table, params):
        assert table == "servers"
        return []  # RLS hides other orgs' rows

    monkeypatch.setattr("app.routers.servers.postgrest_get", fake_get)


@pytest.mark.parametrize(
    "method,path,json_body",
    [
        ("POST", "/api/v1/servers/other-org-srv/test", None),
        ("DELETE", "/api/v1/servers/other-org-srv", None),
        ("GET", "/api/v1/servers/other-org-srv/files", None),
        ("PATCH", "/api/v1/servers/other-org-srv", {"name": "x"}),
        ("GET", "/api/v1/servers/other-org-srv/files/read?path=/etc/hosts", None),
    ],
)
def test_foreign_server_is_404(client, make_token, monkeypatch, method, path, json_body):
    _server_not_visible(monkeypatch)
    resp = client.request(method, path, json=json_body, headers=_auth(make_token))
    assert resp.status_code == 404


def test_foreign_server_files_read_404(client, make_token, monkeypatch):
    _server_not_visible(monkeypatch)
    resp = client.get(
        "/api/v1/servers/other-org-srv/files/read?path=/etc/hosts",
        headers=_auth(make_token),
    )
    assert resp.status_code == 404


# --- WS handshake survives big cookie headers (us-1.29) ---------------------


def test_ws_handshake_header_line_limit_raised():
    """Browsers attach every cookie scoped to the API's hostname to the WS
    upgrade request; a few Supabase auth cookies exceed the websockets
    parser's 8 KB-per-line default, and it then drops the handshake without
    any response — the terminal sits at "Connecting" forever. Importing the
    app must raise the limit."""
    import websockets.http11

    import app.main  # noqa: F401 — importing the app applies the limit

    assert websockets.http11.MAX_LINE_LENGTH >= 65536


# --- create: validation + write-only ---------------------------------------


def test_create_requires_credential(client, make_token, monkeypatch):
    resp = client.post(
        "/api/v1/servers",
        json={
            "org_id": "org-1",
            "name": "prod",
            "host": "h",
            "username": "root",
            "auth_method": "password",
        },
        headers=_auth(make_token),
    )
    assert resp.status_code == 400
    assert "password" in resp.json()["detail"].lower()


def test_create_password_server_never_echoes_secret(client, make_token, monkeypatch):
    written = {}

    async def fake_post(settings, token, table, body):
        assert "password" not in body  # secret must not go into the DB row
        return [{
            "id": "srv-1", "org_id": "org-1", "name": "prod", "host": "h",
            "port": 22, "username": "root", "auth_method": "password",
            "key_fingerprint": None, "host_key_fingerprint": None,
        }]

    async def fake_put(settings, path, content, content_type="application/octet-stream"):
        written[path] = content

    monkeypatch.setattr("app.routers.servers.postgrest_post", fake_post)
    monkeypatch.setattr("app.routers.servers.storage.put_object", fake_put)

    resp = client.post(
        "/api/v1/servers",
        json={
            "org_id": "org-1", "name": "prod", "host": "h", "username": "root",
            "auth_method": "password", "password": "hunter2",
        },
        headers=_auth(make_token),
    )
    assert resp.status_code == 200
    body = resp.json()
    # No secret material anywhere in the response.
    assert "password" not in body
    assert "private_key" not in body
    # Credential landed in the org's private folder.
    assert any(p.endswith("org-1/servers/srv-1/password") for p in written)


def test_create_ssh_key_stores_fingerprint(client, make_token, monkeypatch):
    async def fake_post(settings, token, table, body):
        assert body["key_fingerprint"] == "SHA256:testfp"
        return [{
            "id": "srv-2", "org_id": "org-1", "name": "k", "host": "h",
            "port": 22, "username": "root", "auth_method": "ssh_key",
            "key_fingerprint": "SHA256:testfp", "host_key_fingerprint": None,
        }]

    async def fake_put(settings, path, content, content_type="application/octet-stream"):
        pass

    monkeypatch.setattr("app.routers.servers.postgrest_post", fake_post)
    monkeypatch.setattr("app.routers.servers.storage.put_object", fake_put)
    monkeypatch.setattr("app.ssh.public_key_fingerprint", lambda pem, pp: "SHA256:testfp")

    resp = client.post(
        "/api/v1/servers",
        json={
            "org_id": "org-1", "name": "k", "host": "h", "username": "root",
            "auth_method": "ssh_key", "private_key": "-----BEGIN-----",
        },
        headers=_auth(make_token),
    )
    assert resp.status_code == 200
    assert resp.json()["key_fingerprint"] == "SHA256:testfp"


def test_create_rejects_bad_key(client, make_token, monkeypatch):
    def boom(pem, pp):
        raise ssh.SSHError("Could not read that private key")

    monkeypatch.setattr("app.ssh.public_key_fingerprint", boom)
    resp = client.post(
        "/api/v1/servers",
        json={
            "org_id": "org-1", "name": "k", "host": "h", "username": "root",
            "auth_method": "ssh_key", "private_key": "garbage",
        },
        headers=_auth(make_token),
    )
    assert resp.status_code == 400


# --- test connection + host key TOFU / change ------------------------------


class _FakeConn:
    def __init__(self):
        self.transport = object()
        self.host_key_fingerprint = "SHA256:hostfp"

    def close(self):
        pass


def _server_row(monkeypatch, auth_method="password"):
    async def fake_get(settings, token, table, params):
        return [{
            "id": "srv-1", "org_id": "org-1", "name": "prod", "host": "h",
            "port": 22, "username": "root", "auth_method": auth_method,
            "key_fingerprint": None, "host_key_fingerprint": None,
        }]

    monkeypatch.setattr("app.routers.servers.postgrest_get", fake_get)


def test_test_connection_ok(client, make_token, monkeypatch):
    _server_row(monkeypatch)

    async def fake_connect(settings, token, server):
        return _FakeConn()

    monkeypatch.setattr("app.routers.servers.connect_server", fake_connect)
    resp = client.post("/api/v1/servers/srv-1/test", headers=_auth(make_token))
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_test_connection_host_key_changed_is_409(client, make_token, monkeypatch):
    _server_row(monkeypatch)

    from fastapi import HTTPException

    async def fake_connect(settings, token, server):
        raise HTTPException(status_code=409, detail="host key changed")

    monkeypatch.setattr("app.routers.servers.connect_server", fake_connect)
    resp = client.post("/api/v1/servers/srv-1/test", headers=_auth(make_token))
    assert resp.status_code == 409


# --- US-20.4: dry-run test before the server exists ------------------------


_DRY_RUN_BODY = {
    "host": "1.2.3.4",
    "port": 22,
    "username": "root",
    "auth_method": "password",
    "password": "hunter2",
}


def _capture_open_connection(monkeypatch, *, raises=None):
    """Replace ssh.open_connection and record how it was called."""
    calls = {}

    def fake_open(**kwargs):
        calls.update(kwargs)
        if raises is not None:
            raise raises
        return _FakeConn()

    monkeypatch.setattr("app.ssh.open_connection", fake_open)
    return calls


def _forbid_writes(monkeypatch):
    """Any persistence during a dry run is the bug this guards against."""

    async def boom(*a, **k):  # pragma: no cover - only runs on regression
        raise AssertionError("the dry run must not write anything")

    monkeypatch.setattr("app.routers.servers.postgrest_post", boom)
    monkeypatch.setattr("app.routers.servers.postgrest_patch", boom)
    monkeypatch.setattr("app.storage.put_object", boom, raising=False)


def test_dry_run_requires_auth(client):
    resp = client.post("/api/v1/servers/test-connection", json=_DRY_RUN_BODY)
    assert resp.status_code == 401


def test_dry_run_connects_and_stores_nothing(client, make_token, monkeypatch):
    calls = _capture_open_connection(monkeypatch)
    _forbid_writes(monkeypatch)

    resp = client.post(
        "/api/v1/servers/test-connection",
        json=_DRY_RUN_BODY,
        headers=_auth(make_token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["host_key_fingerprint"] == "SHA256:hostfp"
    # No server row means no trusted key to enforce yet.
    assert calls["expected_host_fingerprint"] is None
    assert calls["host"] == "1.2.3.4"
    # The credential never comes back out.
    assert "hunter2" not in resp.text


def test_dry_run_maps_ssh_failure_to_502(client, make_token, monkeypatch):
    _capture_open_connection(monkeypatch, raises=ssh.SSHError("Authentication failed"))
    resp = client.post(
        "/api/v1/servers/test-connection",
        json=_DRY_RUN_BODY,
        headers=_auth(make_token),
    )
    assert resp.status_code == 502
    assert "Authentication failed" in resp.json()["detail"]


def test_dry_run_enforces_a_known_servers_host_key(client, make_token, monkeypatch):
    """Editing an existing server still honours its trusted host key."""

    async def fake_get(settings, token, table, params):
        return [{
            "id": "srv-1", "org_id": "org-1", "name": "prod", "host": "h",
            "port": 22, "username": "root", "auth_method": "password",
            "key_fingerprint": None, "host_key_fingerprint": "SHA256:trusted",
        }]

    monkeypatch.setattr("app.routers.servers.postgrest_get", fake_get)
    calls = _capture_open_connection(monkeypatch)
    _forbid_writes(monkeypatch)

    resp = client.post(
        "/api/v1/servers/test-connection",
        json={**_DRY_RUN_BODY, "server_id": "srv-1"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 200
    assert calls["expected_host_fingerprint"] == "SHA256:trusted"


def test_dry_run_host_key_changed_is_409(client, make_token, monkeypatch):
    async def fake_get(settings, token, table, params):
        return [{
            "id": "srv-1", "org_id": "org-1", "name": "prod", "host": "h",
            "port": 22, "username": "root", "auth_method": "password",
            "key_fingerprint": None, "host_key_fingerprint": "SHA256:trusted",
        }]

    monkeypatch.setattr("app.routers.servers.postgrest_get", fake_get)
    _capture_open_connection(
        monkeypatch, raises=ssh.HostKeyChanged("host key changed")
    )
    resp = client.post(
        "/api/v1/servers/test-connection",
        json={**_DRY_RUN_BODY, "server_id": "srv-1"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 409


def test_dry_run_foreign_server_id_is_404(client, make_token, monkeypatch):
    """RLS returns no row for another org's server — the same gate as everywhere."""

    async def fake_get(settings, token, table, params):
        return []

    monkeypatch.setattr("app.routers.servers.postgrest_get", fake_get)
    _capture_open_connection(monkeypatch)
    resp = client.post(
        "/api/v1/servers/test-connection",
        json={**_DRY_RUN_BODY, "server_id": "srv-other"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 404


@pytest.mark.parametrize(
    "patch,detail",
    [
        ({"host": "  "}, "Host and username are required."),
        ({"port": 0}, "Port must be between 1 and 65535."),
        ({"password": None}, "Enter a password."),
    ],
)
def test_dry_run_validation(client, make_token, monkeypatch, patch, detail):
    _capture_open_connection(monkeypatch)
    resp = client.post(
        "/api/v1/servers/test-connection",
        json={**_DRY_RUN_BODY, **patch},
        headers=_auth(make_token),
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == detail


# --- file manager error mapping --------------------------------------------


def test_read_binary_file_is_422(client, make_token, monkeypatch):
    _server_row(monkeypatch)

    async def fake_connect(settings, token, server):
        return _FakeConn()

    def fake_read(transport, path):
        raise sftp_ops.NotEditable("This looks like a binary file — download it instead.")

    monkeypatch.setattr("app.routers.servers.connect_server", fake_connect)
    monkeypatch.setattr("app.sftp.read_text", fake_read)

    resp = client.get(
        "/api/v1/servers/srv-1/files/read?path=/bin/ls", headers=_auth(make_token)
    )
    assert resp.status_code == 422


def test_write_conflict_is_409(client, make_token, monkeypatch):
    _server_row(monkeypatch)

    async def fake_connect(settings, token, server):
        return _FakeConn()

    def fake_write(transport, path, content, eol, mtime, size, force):
        raise sftp_ops.SftpConflict("changed on server")

    monkeypatch.setattr("app.routers.servers.connect_server", fake_connect)
    monkeypatch.setattr("app.sftp.write_text", fake_write)

    resp = client.post(
        "/api/v1/servers/srv-1/files/write",
        json={"path": "/tmp/a", "content": "x", "eol": "lf", "expected_mtime": 1, "expected_size": 1},
        headers=_auth(make_token),
    )
    assert resp.status_code == 409


def test_delete_non_empty_folder_is_400(client, make_token, monkeypatch):
    _server_row(monkeypatch)

    async def fake_connect(settings, token, server):
        return _FakeConn()

    def fake_remove(transport, path, recursive):
        raise sftp_ops.SftpError("This folder isn't empty — confirm recursive delete.")

    monkeypatch.setattr("app.routers.servers.connect_server", fake_connect)
    monkeypatch.setattr("app.sftp.remove", fake_remove)

    resp = client.post(
        "/api/v1/servers/srv-1/files/delete",
        json={"path": "/tmp/dir", "recursive": False},
        headers=_auth(make_token),
    )
    assert resp.status_code == 400
    assert "isn't empty" in resp.json()["detail"]


# --- websocket terminal: auth + isolation ----------------------------------


def test_pty_dim_clamps_bad_values():
    from app.routers.servers import _pty_dim

    assert _pty_dim(None, 80) == 80        # missing -> default
    assert _pty_dim(0, 80) == 1            # zero -> min 1 (never a 0-size PTY)
    assert _pty_dim(-5, 24) == 1           # negative -> min 1
    assert _pty_dim(120, 80) == 120        # normal passes through
    assert _pty_dim(99999, 80) == 1000     # absurd -> clamped
    assert _pty_dim("not-a-number", 24) == 24  # garbage -> default


def test_terminal_ws_server_not_found(client, make_token, monkeypatch):
    async def fake_get(settings, token, table, params):
        return []

    monkeypatch.setattr("app.routers.servers.postgrest_get", fake_get)

    with client.websocket_connect("/api/v1/servers/other/terminal") as ws:
        ws.send_text(json.dumps({"token": make_token(), "cols": 80, "rows": 24}))
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert "not found" in msg["message"].lower()
