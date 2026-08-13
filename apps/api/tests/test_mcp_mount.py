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
