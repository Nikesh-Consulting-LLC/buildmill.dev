"""US-52.3: the machine-side Claude connect flow — the script the app installs
and the endpoints that drive it. The SSH round-trips themselves need a live
box; what is pinned here is everything that must be true before one exists."""

from app.routers.servers import connect_claude_script, router


def test_connect_script_shape():
    script = connect_claude_script("/opt/buildmill-agents")
    # Claude Code performs the OAuth — the app never does.
    assert "claude setup-token" in script
    # The token arrives via a hidden read, never argv or history.
    assert "read -rs TOKEN" in script
    # Refused by shape before anything is written.
    assert "sk-ant-oat*" in script
    # Replace-not-append: any prior token line is stripped first.
    assert "sed -i '/^CLAUDE_CODE_OAUTH_TOKEN=/d'" in script
    # Written via a builtin pipe (printf | tee) so it never hits `ps`.
    assert "printf 'CLAUDE_CODE_OAUTH_TOKEN=%s\\n' \"$TOKEN\" | sudo tee -a" in script
    # Every slot's unit restarts so the next claim inherits the env.
    assert 'systemctl restart "buildmill-agent@$i"' in script
    # The workdir is baked in, quoted.
    assert "'/opt/buildmill-agents'" in script
    # And the flow says what to do next.
    assert "Verify connection" in script


def test_connect_script_never_echoes_the_token():
    script = connect_claude_script("/opt/x")
    # No line prints $TOKEN back out — echo of the secret is the one thing
    # this script must never do, in a terminal that may be screen-shared.
    for line in script.splitlines():
        if "echo" in line:
            assert "$TOKEN" not in line


def test_claude_subscription_routes_are_registered():
    paths = {getattr(r, "path", "") for r in router.routes}
    for suffix in ("prepare", "verify", "disconnect"):
        assert f"/servers/{{server_id}}/claude-subscription/{suffix}" in paths
