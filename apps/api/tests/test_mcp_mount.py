"""2026-08-13: /mcp must never answer a redirect.

Release 2026.08.13.1 failed because the claude CLI refused the 307 from the
slashless mount path — newer MCP clients treat a redirected MCP server as a
security failure and report `status: failed`, and the release-prep agent ran
toolless. The mount path is rewritten in-process instead.
"""


def test_slashless_mcp_is_served_not_redirected(client):
    resp = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        follow_redirects=False,
    )
    assert resp.status_code not in (301, 302, 307, 308)


def test_slashless_and_slashed_answer_alike(client):
    bare = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        follow_redirects=False,
    )
    slashed = client.post(
        "/mcp/",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        follow_redirects=False,
    )
    assert bare.status_code == slashed.status_code


# --------------------------------------- us-115.1: MCP's own auth shape works


def test_a_bearer_token_is_read_as_the_worker_token():
    """The mount accepts `Authorization: Bearer <token>` as well as
    `X-Worker-Token`, so a client configured the standard way authenticates —
    and, because it declares an `Authorization` header, skips the four
    `.well-known` OAuth probes this mount can only answer 401/404."""
    from app.factory_mcp import _presented_token

    assert _presented_token({"authorization": "Bearer sfw_abc"}) == "sfw_abc"
    # Case in the scheme is not the client's problem.
    assert _presented_token({"authorization": "bearer sfw_abc"}) == "sfw_abc"
    assert _presented_token({"authorization": "BEARER sfw_abc"}) == "sfw_abc"


def test_the_header_still_wins_and_nothing_else_is_accepted():
    """Every worker, runner and copy-paste snippet in the wild sends
    `X-Worker-Token`; it is checked first and is unchanged."""
    from app.factory_mcp import _presented_token

    assert _presented_token({"x-worker-token": "sfw_header"}) == "sfw_header"
    assert (
        _presented_token(
            {"x-worker-token": "sfw_header", "authorization": "Bearer sfw_bearer"}
        )
        == "sfw_header"
    )
    # Not a bearer, not a credential.
    assert _presented_token({"authorization": "Basic sfw_abc"}) == ""
    assert _presented_token({"authorization": "sfw_abc"}) == ""
    assert _presented_token({}) == ""


def test_a_bearer_token_the_registry_does_not_know_is_still_401(client, monkeypatch):
    """A second accepted spelling is not a second policy: an unknown credential
    is refused in either form, and the registry is what decides."""
    asked: list[str] = []

    def _no_such_worker(settings, token):
        asked.append(token)
        return None

    monkeypatch.setattr("app.factory_mcp.db.get_worker_by_token", _no_such_worker)

    resp = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Authorization": "Bearer not-a-real-token"},
        follow_redirects=False,
    )
    assert resp.status_code == 401
    # The bearer VALUE is what was looked up — not the whole header.
    assert asked == ["not-a-real-token"]
