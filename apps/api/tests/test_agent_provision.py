"""Agent server provisioning engine (Phase 26, US-26.1–26.9).

The SSH layer is faked — these cover the parts that decide what runs on
someone's machine and what ends up in a log any org member can read:
content-addressed versioning, redaction, sudo handling, preflight refusals,
probe parsing, and the unit file itself.
"""

from __future__ import annotations

import os
import tarfile

import pytest

from app import agent_provision as ap


# ---------------------------------------------------------------------------
# A fake paramiko transport: scripted (status, output) per command
# ---------------------------------------------------------------------------


class FakeChannel:
    def __init__(self, transport: "FakeTransport"):
        self.transport = transport
        self.command = ""
        self.stdin = b""

    def set_combine_stderr(self, _flag):  # noqa: D102
        pass

    def exec_command(self, command):
        self.command = command
        self.transport.commands.append(command)

    def sendall(self, data):
        self.stdin += data
        self.transport.stdin.append(data)

    def shutdown_write(self):
        pass

    def makefile(self, _mode):
        status, output = self.transport.respond(self.command)
        self._status = status
        return [f"{line}\n".encode() for line in output.splitlines()]

    def recv_exit_status(self):
        return self._status

    def close(self):
        pass


class FakeTransport:
    def __init__(self, responder=None):
        self.commands: list[str] = []
        self.stdin: list[bytes] = []
        self._responder = responder or (lambda _cmd: (0, ""))

    def respond(self, command):
        result = self._responder(command)
        return result if result is not None else (0, "")

    def open_session(self):
        return FakeChannel(self)


DEBIAN = {
    "cat /etc/os-release": (0, 'ID=ubuntu\nID_LIKE=debian\nPRETTY_NAME="Ubuntu 24.04 LTS"'),
    "command -v systemctl": (0, ""),
    "id -u": (0, "0"),
    "uname -m": (0, "x86_64"),
}


def debian_responder(command: str):
    for needle, result in DEBIAN.items():
        if needle in command:
            return result
    if "df -Pk" in command:
        return (0, "52428800")  # 50 GB in KB
    return (0, "")


# ---------------------------------------------------------------------------
# The bundle: the hash IS the version (US-26.2 / US-26.8)
# ---------------------------------------------------------------------------


def _tree(tmp_path, files: dict[str, str]):
    for rel, body in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    return str(tmp_path)


def test_bundle_hash_is_content_addressed(tmp_path):
    a = _tree(tmp_path / "a", {"supervisor/__main__.py": "print(1)\n", "README.md": "hi"})
    b = _tree(tmp_path / "b", {"supervisor/__main__.py": "print(1)\n", "README.md": "hi"})
    # same content in two checkouts -> same version, so drift is a real
    # comparison and not a race against file mtimes
    assert ap.bundle_hash(a) == ap.bundle_hash(b)


def test_bundle_hash_changes_with_content(tmp_path):
    before = _tree(tmp_path / "a", {"supervisor/__main__.py": "print(1)\n"})
    hash_before = ap.bundle_hash(before)
    (tmp_path / "a" / "supervisor" / "__main__.py").write_text("print(2)\n")
    assert ap.bundle_hash(before) != hash_before


def test_bundle_ignores_caches_and_workspaces(tmp_path):
    root = _tree(
        tmp_path / "a",
        {
            "supervisor/__main__.py": "print(1)\n",
            "supervisor/__pycache__/x.pyc": "junk",
            "supervisor/workspace/issue-1/notes.txt": "someone's work in progress",
        },
    )
    digest = ap.bundle_hash(root)
    (tmp_path / "a" / "supervisor" / "workspace" / "issue-1" / "notes.txt").write_text("more")
    assert ap.bundle_hash(root) == digest


def test_build_bundle_contains_the_tree_and_is_reproducible(tmp_path):
    root = _tree(tmp_path / "a", {"supervisor/__main__.py": "print(1)\n", "requirements.txt": "httpx\n"})
    path, digest = ap.build_bundle(root)
    try:
        with tarfile.open(path) as tar:
            names = sorted(tar.getnames())
            assert names == ["requirements.txt", "supervisor/__main__.py"]
            # fixed metadata: two builds of one tree produce identical archives
            assert all(tar.getmember(n).mtime == 0 for n in names)
        assert digest == ap.bundle_hash(root)
    finally:
        os.unlink(path)


def test_the_real_runner_tree_is_hashable():
    """The bundle source must exist where the API expects it, or every
    provision fails at push time on a machine nobody can debug from here."""
    root = ap.runner_source_dir()
    assert os.path.isdir(root), root
    assert os.path.isfile(os.path.join(root, "requirements.txt"))
    assert len(ap.bundle_hash()) == 16


# ---------------------------------------------------------------------------
# Redaction: the log is readable by any org member (US-26.2)
# ---------------------------------------------------------------------------


def test_masker_hides_known_secrets_longest_first():
    mask = ap.make_masker(["hunter2secret", "hunter2"])
    assert "hunter2" not in mask("password is hunter2secret ok")


def test_masker_hides_worker_tokens_it_was_never_told_about():
    mask = ap.make_masker([])
    line = mask("FACTORY_WORKER_TOKEN=sfw_0123456789abcdef0123")
    assert "sfw_0123456789abcdef0123" not in line
    assert "sfw_" in line  # still legible as "a token was here"


def test_masker_ignores_trivially_short_values():
    mask = ap.make_masker(["a", None, ""])
    assert mask("a plain line") == "a plain line"


def test_run_masks_both_the_command_and_its_output():
    transport = FakeTransport(lambda cmd: (0, "echoing hunter2 back"))
    logged: list[str] = []
    mask = ap.make_masker(["hunter2"])
    ap.run(
        transport,
        "echo hunter2",
        log=logged.append,
        mask=mask,
    )
    assert all("hunter2" not in line for line in logged), logged


# ---------------------------------------------------------------------------
# sudo (US-26.2)
# ---------------------------------------------------------------------------


def test_sudo_password_goes_on_stdin_not_the_command_line():
    transport = FakeTransport()
    ap.run(transport, "apt-get update", sudo=True, password="hunter2")
    assert transport.commands, "nothing ran"
    assert "hunter2" not in transport.commands[0]  # `ps` on the target would show it
    assert b"hunter2\n" in transport.stdin[0]
    assert "-S" in transport.commands[0]


def test_sudo_without_a_password_requires_passwordless():
    transport = FakeTransport()
    ap.run(transport, "apt-get update", sudo=True, password=None)
    assert "-n" in transport.commands[0]
    assert not transport.stdin


def test_run_ok_raises_a_named_step_on_failure():
    transport = FakeTransport(lambda cmd: (1, "E: Unable to locate package nope"))
    with pytest.raises(ap.JobError) as e:
        ap.run_ok(transport, "apt-get install nope", step="extra packages")
    assert e.value.step == "extra packages"
    assert "Unable to locate package" in e.value.message


# ---------------------------------------------------------------------------
# Preflight refuses early and specifically (US-26.1)
# ---------------------------------------------------------------------------


REACHABLE_API = "https://api.buildmill.dev"


def reachable_responder(command: str):
    """The debian box, plus a factory it can reach (US-27.13)."""
    if "curl" in command and "/api/v1/health" in command:
        return (0, "200")
    return debian_responder(command)


def test_preflight_passes_on_a_debian_box():
    checks = ap.preflight(
        FakeTransport(reachable_responder), "/opt/buildmill", None, REACHABLE_API
    )
    assert all(c["ok"] for c in checks), checks
    assert {c["check"] for c in checks} == {
        "ssh",
        "os",
        "systemd",
        "sudo",
        "arch",
        "disk",
        "factory-reachable",
    }


# ---------------------------------------------------------------------------
# US-27.13: the machine has to be able to reach the factory
# ---------------------------------------------------------------------------


def test_a_loopback_api_address_is_refused_without_testing_it():
    """The first agent server provisioned cleanly and produced two agents
    told to dial http://localhost:8000 — the API's own default. No remote
    machine can reach that, so there is nothing to test."""
    for url in ("http://localhost:8000", "http://127.0.0.1:8000", ""):
        problem = ap.api_url_problem(url)
        assert problem, url
        assert "API_BASE_URL" in problem
        assert "apps/api/.env" in problem


def test_a_real_address_is_not_refused_out_of_hand():
    assert ap.api_url_problem("https://api.buildmill.dev") is None


def test_preflight_reports_a_machine_that_cannot_reach_the_factory():
    def unreachable(command: str):
        if "curl" in command:
            return (6, "curl: (6) Could not resolve host")
        return debian_responder(command)

    checks = {
        c["check"]: c
        for c in ap.preflight(
            FakeTransport(unreachable), "/opt/buildmill", None, REACHABLE_API
        )
    }
    assert not checks["factory-reachable"]["ok"]
    assert "could not reach" in checks["factory-reachable"]["detail"]
    assert "api.buildmill.dev" in checks["factory-reachable"]["detail"]


def test_preflight_names_the_health_url_it_reached():
    checks = {
        c["check"]: c
        for c in ap.preflight(
            FakeTransport(reachable_responder), "/opt/buildmill", None, REACHABLE_API
        )
    }
    assert checks["factory-reachable"]["ok"]
    assert "/api/v1/health" in checks["factory-reachable"]["detail"]


def test_an_update_never_repoints_a_working_slot_at_a_loopback_url():
    """This happened. On 2026-07-26 the deployed API had no API_BASE_URL, so
    an Update "converged" two working agents from https://api.buildmill.dev to
    http://localhost:8000 — re-issuing both tokens on the way — and the fleet
    went down. A convergence that makes a machine worse is not convergence."""
    import inspect

    source = inspect.getsource(ap._job_update)
    assert "api_url_problem" in source
    # the slot keeps what it has rather than being pointed at the loopback
    assert "NOT " in source and "re-pointed" in source


def test_a_slot_that_never_connects_names_the_url_it_was_told_to_dial():
    """US-26.4's connect timeout used to read as "the agent is broken" when
    the fact was that the address it was handed does not resolve from that
    machine. Covered here so the us-26.8 fix cannot regress."""
    msg = ap.connect_timeout_message(2, "https://api.buildmill.dev")
    assert "slot 2" in msg
    assert "https://api.buildmill.dev" in msg
    assert "API_BASE_URL" in msg


def test_preflight_carries_the_loopback_refusal_as_its_own_check():
    checks = {
        c["check"]: c
        for c in ap.preflight(
            FakeTransport(debian_responder),
            "/opt/buildmill",
            None,
            "http://localhost:8000",
        )
    }
    assert not checks["factory-reachable"]["ok"]
    assert "loopback" in checks["factory-reachable"]["detail"]


def test_preflight_names_a_non_debian_machine():
    def responder(command):
        if "os-release" in command:
            return (0, 'ID=alpine\nPRETTY_NAME="Alpine Linux v3.20"')
        return debian_responder(command)

    checks = {c["check"]: c for c in ap.preflight(FakeTransport(responder), "/opt/buildmill", None)}
    assert not checks["os"]["ok"]
    assert "alpine" in checks["os"]["detail"]


def test_preflight_names_a_user_that_cannot_sudo():
    def responder(command):
        if "sudo" in command:
            return (1, "user is not in the sudoers file")
        return debian_responder(command)

    checks = {c["check"]: c for c in ap.preflight(FakeTransport(responder), "/opt/buildmill", None)}
    assert not checks["sudo"]["ok"]
    assert "sudo" in checks["sudo"]["detail"]


def test_preflight_names_a_machine_without_systemd():
    def responder(command):
        if "command -v systemctl" in command:
            return (1, "")
        return debian_responder(command)

    checks = {c["check"]: c for c in ap.preflight(FakeTransport(responder), "/opt/buildmill", None)}
    assert not checks["systemd"]["ok"]


# ---------------------------------------------------------------------------
# Probe (US-26.7)
# ---------------------------------------------------------------------------


PROBE_OUTPUT = """os=Ubuntu 24.04 LTS
cpu=4
mem_total=7943
mem_free=5210
load=0.42
disk_total=48.42
disk_free=31.07
bundle=abc123def4567890
"""


def test_probe_parses_the_machine():
    def responder(command):
        if "is-active" in command:
            return (0, "active" if "@1" in command else "failed")
        return (0, PROBE_OUTPUT)

    result = ap.probe_host(FakeTransport(responder), "/opt/buildmill", [1, 2])
    assert result["cpu_count"] == 4
    assert result["mem_free_mb"] == 5210
    assert result["disk_free_gb"] == 31.07
    assert result["load_avg"] == 0.42
    assert result["bundle_hash"] == "abc123def4567890"
    assert result["services"] == {1: "active", 2: "failed"}


def test_probe_survives_a_machine_that_answers_junk():
    def responder(command):
        if "is-active" in command:
            return (0, "nonsense-state")
        return (0, "os=Weird\ncpu=lots\ndisk_free=\n")

    result = ap.probe_host(FakeTransport(responder), "/opt/buildmill", [1])
    assert result["cpu_count"] is None
    assert result["disk_free_gb"] is None
    assert result["services"] == {1: "unknown"}  # never an unconstrained value


def test_probe_never_mutates_the_machine():
    transport = FakeTransport(lambda cmd: (0, PROBE_OUTPUT))
    ap.probe_host(transport, "/opt/buildmill", [1])
    joined = " ".join(transport.commands)
    for verb in ("rm ", "apt-get", "systemctl restart", "systemctl stop", "mkdir", "> /"):
        assert verb not in joined, f"probe ran a mutating command: {verb}"


def test_probe_failure_is_a_named_step():
    with pytest.raises(ap.JobError) as e:
        ap.probe_host(FakeTransport(lambda cmd: (127, "sh: not found")), "/opt/buildmill", [])
    assert e.value.step == "probe"


# ---------------------------------------------------------------------------
# The unit file and the env file (US-26.2 / US-26.4)
# ---------------------------------------------------------------------------


def test_unit_runs_as_the_service_user_from_the_env_file():
    unit = ap.unit_file("/opt/buildmill")
    assert f"User={ap.SERVICE_USER}" in unit
    assert "User=root" not in unit
    assert "EnvironmentFile=/opt/buildmill/env/%i.env" in unit
    assert "ExecStart=/opt/buildmill/venv/bin/python -m supervisor" in unit
    assert "WorkingDirectory=/opt/buildmill/app/current" in unit
    assert "Restart=always" in unit


def test_slot_env_is_written_0600_and_the_token_is_not_echoed():
    transport = FakeTransport()
    logged: list[str] = []
    ctx = ap.StepCtx(
        transport=transport,
        host={"workdir": "/opt/buildmill"},
        password=None,
        log=logged.append,
        mask=ap.make_masker([]),
    )
    ap.write_slot_env(ctx, 2, "sfw_deadbeefdeadbeefdeadbeef", "https://api.example.com")

    command = transport.commands[0]
    assert "chmod 600 /opt/buildmill/env/2.env" in command
    assert f"chown {ap.SERVICE_USER}:{ap.SERVICE_USER}" in command
    # the heredoc carries the token, so the command must never be logged
    assert all("sfw_deadbeef" not in line for line in logged), logged


def test_agent_sudo_is_off_unless_asked_for():
    transport = FakeTransport()
    ctx = ap.StepCtx(
        transport=transport,
        host={"workdir": "/opt/buildmill", "allow_agent_sudo": False},
        password=None,
        log=lambda _l: None,
        mask=ap.make_masker([]),
    )
    ap.install_service_user(ctx)
    joined = " ".join(transport.commands)
    assert "NOPASSWD" not in joined
    assert "rm -f /etc/sudoers.d/buildmill-agent" in joined  # turning it off takes it away


def test_agent_sudo_writes_a_drop_in_when_it_is_on():
    transport = FakeTransport()
    ctx = ap.StepCtx(
        transport=transport,
        host={"workdir": "/opt/buildmill", "allow_agent_sudo": True},
        password=None,
        log=lambda _l: None,
        mask=ap.make_masker([]),
    )
    ap.install_service_user(ctx)
    assert "NOPASSWD" in " ".join(transport.commands)


def test_install_service_user_does_not_recursively_chown_the_agents_directory():
    """US-57.4 regression guard: this step re-runs on every provision AND
    every Update (_job_update). A recursive chown of the whole workdir would
    silently revert a shared host's per-slot ownership on the next Update —
    the workdir and app/env dirs converge to SERVICE_USER; agents/ does not."""
    transport = FakeTransport()
    ctx = ap.StepCtx(
        transport=transport,
        host={"workdir": "/opt/buildmill", "allow_agent_sudo": False},
        password=None,
        log=lambda _l: None,
        mask=ap.make_masker([]),
    )
    ap.install_service_user(ctx)
    joined = " ".join(transport.commands)
    # the workdir itself (non-recursive) and app/env (recursive) converge to
    # SERVICE_USER...
    assert f"chown {ap.SERVICE_USER}:{ap.SERVICE_USER} /opt/buildmill &&" in joined
    assert f"chown -R {ap.SERVICE_USER}:{ap.SERVICE_USER} /opt/buildmill/app /opt/buildmill/env" in joined
    # ...but no chown -R names a path under (or equal to) agents/, and no
    # chown -R targets the bare workdir (which would sweep agents/ too)
    assert f"chown -R {ap.SERVICE_USER}:{ap.SERVICE_USER} /opt/buildmill/agents" not in joined
    assert f"chown -R {ap.SERVICE_USER}:{ap.SERVICE_USER} /opt/buildmill &&" not in joined
    assert f"chown -R {ap.SERVICE_USER}:{ap.SERVICE_USER} /opt/buildmill\n" not in joined


def test_write_slot_env_defaults_to_the_shared_service_user():
    """A non-shared host (user=None) keeps exactly Phase 26's behavior."""
    transport = FakeTransport()
    ctx = _ctx(transport)
    ap.write_slot_env(ctx, 3, "sfw_deadbeefdeadbeefdeadbeef", "https://api.example.com")
    command = transport.commands[0]
    assert f"chown {ap.SERVICE_USER}:{ap.SERVICE_USER} /opt/buildmill/env/3.env" in command
    assert f"chown -R {ap.SERVICE_USER}:{ap.SERVICE_USER} /opt/buildmill/agents/3" in command


def test_write_slot_env_chowns_to_the_slots_own_user_on_a_shared_host():
    transport = FakeTransport()
    ctx = _ctx(transport)
    ap.write_slot_env(
        ctx, 3, "sfw_deadbeefdeadbeefdeadbeef", "https://api.example.com", user="bm-agent-3"
    )
    command = transport.commands[0]
    assert "chown bm-agent-3:bm-agent-3 /opt/buildmill/env/3.env" in command
    assert "chown -R bm-agent-3:bm-agent-3 /opt/buildmill/agents/3" in command
    assert "chmod 700 /opt/buildmill/agents/3/workspace" in command
    assert f"{ap.SERVICE_USER}:{ap.SERVICE_USER}" not in command


def test_ensure_slot_workspace_defaults_to_the_shared_service_user():
    transport = FakeTransport()
    ap.ensure_slot_workspace(_ctx(transport), 3)
    command = transport.commands[0]
    assert f"chown -R {ap.SERVICE_USER}:{ap.SERVICE_USER} /opt/buildmill/agents/3" in command
    assert "chmod 700 /opt/buildmill/agents/3/workspace" in command
    assert "mkdir -p /opt/buildmill/agents/3/workspace" in command


def test_ensure_slot_workspace_chowns_to_the_slots_own_user_on_a_shared_host():
    transport = FakeTransport()
    ap.ensure_slot_workspace(_ctx(transport), 3, "bm-agent-3")
    command = transport.commands[0]
    assert "chown -R bm-agent-3:bm-agent-3 /opt/buildmill/agents/3" in command
    assert f"{ap.SERVICE_USER}:{ap.SERVICE_USER}" not in command


def test_ensure_slot_workspace_never_touches_the_env_file():
    """Distinct from write_slot_env: no token, no env file, safe to re-run on
    every routine Update/Restart regardless of whether the API URL matches."""
    transport = FakeTransport()
    ap.ensure_slot_workspace(_ctx(transport), 3, "bm-agent-3")
    joined = " ".join(transport.commands)
    assert "env/3.env" not in joined
    assert "FACTORY_API_URL" not in joined


def test_job_update_and_restart_self_heal_workspace_ownership():
    """The gap this closes: a workspace whose ownership drifted used to stay
    broken through a normal Update or Restart — only provision, add_slot, or
    a token re-issue ever touched it. Both job bodies must now call the
    self-heal unconditionally, not only when the API URL happens to drift."""
    import inspect

    assert "ensure_slot_workspace" in inspect.getsource(ap._job_update)
    assert "ensure_slot_workspace" in inspect.getsource(ap._job_restart)


def test_next_auto_repair_action_climbs_the_ladder_and_then_stops():
    assert ap.next_auto_repair_action(0) == "restart"
    assert ap.next_auto_repair_action(1) == "reissue_token"
    assert ap.next_auto_repair_action(2) == "update"
    assert ap.next_auto_repair_action(3) is None
    assert ap.next_auto_repair_action(100) is None


def test_slot_unix_user_is_named_per_index():
    assert ap.slot_unix_user(7) == "bm-agent-7"
    assert ap.slot_unix_user(1) != ap.slot_unix_user(2)


def test_ensure_slot_user_creates_a_per_slot_account_with_its_own_home():
    transport = FakeTransport()
    ctx = _ctx(transport)
    user = ap.ensure_slot_user(ctx, 4)
    assert user == "bm-agent-4"
    command = transport.commands[0]
    assert "useradd --system --create-home" in command
    assert "bm-agent-4" in command


def test_write_slot_service_override_scopes_to_one_instance_and_reloads():
    """The shared unit template stays untouched — an instance-scoped drop-in
    is what varies the user, so every other slot's unit is unaffected."""
    transport = FakeTransport()
    ctx = _ctx(transport)
    ap.write_slot_service_override(ctx, 5, "bm-agent-5")
    command = transport.commands[0]
    assert "/etc/systemd/system/buildmill-agent@5.service.d" in command
    assert "User=bm-agent-5" in command
    assert "Group=bm-agent-5" in command
    assert "systemctl daemon-reload" in command
    # never touches the shared template file itself
    assert ap.UNIT_TEMPLATE_NAME not in command


# ---------------------------------------------------------------------------
# Env convergence (US-26.8) — the live defect: every agent was told to dial
# http://localhost:8000 because API_BASE_URL was unset on the API host, and
# nothing but delete-and-re-add could repair the slots afterwards.
# ---------------------------------------------------------------------------


def _ctx(transport, logged=None):
    return ap.StepCtx(
        transport=transport,
        host={"workdir": "/opt/buildmill"},
        password=None,
        log=(logged.append if logged is not None else (lambda _l: None)),
        mask=ap.make_masker([]),
    )


def test_read_slot_api_url_reads_what_the_machine_was_told():
    transport = FakeTransport(
        lambda cmd: (0, "FACTORY_API_URL=http://localhost:8000")
    )
    assert ap.read_slot_api_url(_ctx(transport), 1) == "http://localhost:8000"


def test_read_slot_api_url_needs_sudo_and_reads_only_that_line():
    """The token lives in the same 0600 file — only the URL line is read."""
    transport = FakeTransport(lambda cmd: (0, "FACTORY_API_URL=https://api.example.com"))
    ap.read_slot_api_url(_ctx(transport), 2)
    command = transport.commands[0]
    assert "sudo" in command
    assert "FACTORY_API_URL" in command
    assert "/opt/buildmill/env/2.env" in command
    assert "FACTORY_WORKER_TOKEN" not in command


def test_read_slot_api_url_is_none_when_there_is_no_env_file():
    transport = FakeTransport(lambda cmd: (2, ""))
    assert ap.read_slot_api_url(_ctx(transport), 1) is None


def test_read_slot_api_url_is_none_when_the_line_is_missing():
    transport = FakeTransport(lambda cmd: (0, "RUNNER_WORKSPACE=/opt/buildmill/agents/1"))
    assert ap.read_slot_api_url(_ctx(transport), 1) is None


def test_the_connect_timeout_still_reads_from_the_slot_creation_path():
    """The message is built where the slot is started, not somewhere a
    refactor could quietly disconnect it from."""
    import inspect

    assert "connect_timeout_message" in inspect.getsource(ap._create_and_start_slot)


def test_an_adopted_slot_still_follows_the_workers_own_org():
    """US-57.3: the slot belongs to whoever the agent belongs to — on a
    shared pool that is the tenant placing it, not the host's (platform)
    org, and `_create_and_start_slot` is the one place both the fresh-
    identity path and the adopt path meet. A full run needs a live machine
    (agent_provision.py:1 says so of itself), so this pins the source
    rather than claiming a DB-writing execution this file cannot mock
    cheaply — the live-SQL suite (test_agent_pools_sql.py) proves the
    resulting row lands where RLS says it must."""
    import inspect

    source = inspect.getsource(ap._create_and_start_slot)
    assert "org_id = str(row[\"org_id\"])" in source
    # the reassignment must be reached only from the adopt branch, not the
    # fresh-identity branch below it
    adopt_branch, _, fresh_branch = source.partition("else:")
    assert "org_id = str(row" in adopt_branch
    assert "org_id = str(row" not in fresh_branch


# ---------------------------------------------------------------------------
# US-57.8: a job always reaches a terminal state, even before it connects
# ---------------------------------------------------------------------------


def test_a_missing_credential_fails_the_job_instead_of_leaving_it_running(monkeypatch):
    """Live regression, 2026-07-31: re-homing Pod-001 to the platform org
    moved where its credential is expected to live, so `_resolve_password`
    raised `PipelineError` — but that call used to run BEFORE this
    function's try/finally, so the exception propagated out of `run_job`
    uncaught. The job never reached `finally`, stuck at 'running' with an
    empty log, and its one-job-per-host constraint blocked every job after
    it — including the next agent's pool placement."""
    import asyncio

    from app import deploy

    updates: list[dict] = []

    async def missing_credential(settings, host):
        raise deploy.PipelineError("This server has no stored password.")

    monkeypatch.setattr(
        ap,
        "get_host",
        lambda settings, host_id: {
            "id": host_id,
            "org_id": "org-1",
            "server_id": "server-1",
            "server_name": "pod-001",
            "host": "10.0.0.1",
            "workdir": "/opt/buildmill",
            "auth_method": "password",
            "host_key_fingerprint": None,
        },
    )
    monkeypatch.setattr(
        ap, "update_job", lambda settings, job_id, fields: updates.append(fields)
    )
    monkeypatch.setattr(ap, "update_host", lambda settings, host_id, fields: None)
    monkeypatch.setattr(ap, "_resolve_password", missing_credential)

    asyncio.run(
        ap.run_job(
            settings=object(),
            ctx={"job_id": "job-1", "agent_server_id": "host-1", "kind": "probe"},
        )
    )

    final = updates[-1]
    assert final["status"] == "failed"
    assert "no stored password" in final["error"]
    assert final["finished_at"]


# ---------------------------------------------------------------------------
# US-27.9: re-issuing a managed agent's token
# ---------------------------------------------------------------------------


def test_reissue_writes_the_env_before_it_restarts_the_service():
    """The ordering IS the job. A service restarted before the new token is on
    disk comes back holding the dead one, and the manager watches it fail
    twice for no reason."""
    import asyncio

    transport = FakeTransport(debian_responder)
    ctx = _ctx(transport)
    order: list[str] = []

    class Recorder:
        def __getattr__(self, name):
            raise AttributeError(name)

    def fake_reissue(settings, worker_id, **kw):
        order.append("mint")
        return "sfw_newtoken0123456789abcdef"

    # US-75.1: `**kw` so the fake follows write_slot_env's keyword-only extras
    # (us-57.4 added `user`) instead of raising TypeError the next time one is
    # added. The ordering this test is about does not depend on them.
    def fake_write(step, index, token, api_url, **kw):
        order.append(f"write:{token[:8]}")

    async def fake_wait(settings, worker_id, log):
        order.append("wait-for-connect")
        return True

    slot = {
        "id": "slot-1",
        "slot_index": 2,
        "worker_id": "worker-1",
        "name": "pod-001-2",
    }

    original = (
        ap.reissue_worker_token,
        ap.write_slot_env,
        ap._get_slot,
        ap._wait_for_connect,
        ap.run_ok,
    )
    try:
        ap.reissue_worker_token = fake_reissue
        ap.write_slot_env = fake_write
        ap._get_slot = lambda settings, slot_id: slot
        ap._wait_for_connect = fake_wait

        def fake_run_ok(transport, command, **kw):
            order.append(f"shell:{command}")
            return 0, []

        ap.run_ok = fake_run_ok

        class Settings:
            api_base_url = "https://api.buildmill.dev"
            database_url = ""

        outcome = asyncio.run(
            ap._job_reissue_token(
                Settings(), ctx, {"slot_id": "slot-1"}, lambda: None
            )
        )
    finally:
        (
            ap.reissue_worker_token,
            ap.write_slot_env,
            ap._get_slot,
            ap._wait_for_connect,
            ap.run_ok,
        ) = original

    assert outcome == "succeeded"
    assert order[0] == "mint"
    assert order[1].startswith("write:")
    assert "systemctl restart buildmill-agent@2" in order[2]
    assert order[3] == "wait-for-connect"


def test_reissue_refuses_while_the_factory_address_is_loopback():
    """Writing a fresh token pointed at localhost would swap one broken agent
    for another (US-27.13)."""
    import asyncio

    slot = {"id": "s", "slot_index": 1, "worker_id": "w", "name": "n"}
    original = ap._get_slot
    try:
        ap._get_slot = lambda settings, slot_id: slot

        class Settings:
            api_base_url = "http://localhost:8000"
            database_url = ""

        with pytest.raises(ap.JobError) as e:
            asyncio.run(
                ap._job_reissue_token(
                    Settings(), _ctx(FakeTransport()), {"slot_id": "s"}, lambda: None
                )
            )
        assert "API_BASE_URL" in e.value.message
    finally:
        ap._get_slot = original


# ---------------------------------------------------------------------------
# Identity + outcome mapping
# ---------------------------------------------------------------------------


def test_minted_token_matches_the_shape_the_rpc_produces():
    import hashlib

    token, token_hash, last4 = ap.mint_worker_token()
    assert token.startswith("sfw_") and len(token) == 4 + 48
    assert token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert last4 == token[-4:]


def test_two_tokens_are_never_the_same():
    assert ap.mint_worker_token()[0] != ap.mint_worker_token()[0]


def test_adopting_an_agent_pauses_it_but_a_plain_reissue_does_not():
    """An update re-issues a token to re-point a slot; it must not silently
    pause an agent that was enabled — the update restores state itself."""
    import inspect

    assert "pause=True" in inspect.getsource(ap.adopt_worker_token)
    reissue = inspect.getsource(ap.reissue_worker_token)
    assert "pause: bool | None = None" in reissue
    assert "if pause is not None:" in reissue


@pytest.mark.parametrize(
    "kind,outcome,expected",
    [
        ("provision", "succeeded", "ready"),
        ("provision", "failed", "error"),
        # an update that skipped a busy slot is neither a success nor a
        # failure — saying "ready" would hide a box left on old code
        ("update", "partial", "degraded"),
        ("teardown", "succeeded", "removed"),
        ("teardown", "failed", "error"),
    ],
)
def test_host_status_after_a_job(kind, outcome, expected):
    assert ap._host_status_after(kind, outcome) == expected


def test_known_modules_match_the_supervisors_built_ins():
    # the runner ships claude / buildmill / grok / opencode / interactive / sim
    # (apps/runner/README.md; buildmill is Claude Code under a
    # platform-billed name, US-60.1; interactive is the ACP session agent,
    # US-78.3). Neither grok nor interactive is npm-installed — both ship a
    # standalone binary via their own release script, not a package registry.
    assert ap.KNOWN_MODULES == {
        "claude",
        "buildmill",
        "grok",
        "opencode",
        "interactive",
        "sim",
    }
    assert set(ap.MODULE_NPM_PACKAGES) == {"claude", "buildmill", "opencode"}
    assert "grok" in ap.MODULE_BINARIES
    assert "grok" not in ap.MODULE_NPM_PACKAGES


def test_every_installable_module_declares_how_it_is_installed():
    """US-78.1: `install_modules` used to filter to what it recognised, so a
    module in neither table was silently skipped — provisioning reported
    success and the CLI was simply absent. Every module must now be covered by
    exactly one strategy."""
    installable = ap.KNOWN_MODULES - {"sim"}
    npm = set(ap.MODULE_NPM_PACKAGES)
    scripted = set(ap.MODULE_INSTALL_COMMANDS)
    assert installable == npm | scripted
    assert not (npm & scripted), "a module cannot be installed two ways"
    for module in installable:
        assert module in ap.MODULE_BINARIES


def test_the_interactive_cli_never_shares_the_grok_binary_name():
    """US-78.1: both live on every pool machine. One name for two programs is
    a coin flip over which agent answers."""
    assert ap.MODULE_BINARIES["interactive"] == "buildmill-agent-cli"
    assert ap.MODULE_BINARIES["interactive"] != ap.MODULE_BINARIES["grok"]
    cmd = ap.MODULE_INSTALL_COMMANDS["interactive"]
    # the lesson from GROK_CLI_INSTALL_CMD: copy the bytes out of root's 0700
    # home, never symlink into it, or the CLI exists and cannot be executed
    assert "cp -f" in cmd and "ln -s" not in cmd
    assert "/usr/local/bin/buildmill-agent-cli" in cmd


def test_the_interactive_install_undoes_the_symlinks_it_plants():
    """US-78.1, measured live on Pod-001 2026-08-11: xAI's installer symlinks
    /usr/local/bin/grok and /usr/local/bin/agent UNCONDITIONALLY, ignoring HOME.
    On a host that also runs Grok Build that swaps superagent-ai's 1.1.7 for
    xAI's 1.0.0 — a different program — and silently breaks every existing
    Grok Build agent. One Update broke three."""
    cmd = ap.MODULE_INSTALL_COMMANDS["interactive"]
    assert "rm -f /usr/local/bin/agent /usr/local/bin/grok" in cmd
    # and the real grok is put back, but only on a host that has one
    assert "/root/.grok/bin/grok" in cmd
    assert "if [ -x /root/.grok/bin/grok ]" in cmd


def test_the_interactive_install_passes_no_version_where_a_flag_was_meant():
    """`--no-modify-path` is superagent-ai's flag; this installer reads its
    first positional argument as a VERSION and rejected it outright."""
    assert "--no-modify-path" not in ap.MODULE_INSTALL_COMMANDS["interactive"]
    # it is still correct for the OTHER installer, which does take it
    assert "--no-modify-path" in ap.GROK_CLI_INSTALL_CMD


def test_an_unknown_module_fails_provisioning_instead_of_being_skipped():
    transport = FakeTransport()
    ctx = ap.StepCtx(
        transport=transport,
        host={"workdir": "/opt/buildmill", "modules": ["claude", "not-a-module"]},
        password=None,
        log=lambda _l: None,
        mask=ap.make_masker([]),
    )
    with pytest.raises(ap.JobError) as caught:
        ap.install_modules(ctx)
    assert "not-a-module" in str(caught.value)


def test_a_slot_env_isolates_the_interactive_agents_home():
    """US-78.1: on a shared pool machine the slots are different orgs' agents.
    A shared GROK_HOME would be a cross-tenant leak of session transcripts."""
    transport = FakeTransport()
    ctx = ap.StepCtx(
        transport=transport,
        host={"workdir": "/opt/buildmill", "modules": []},
        password=None,
        log=lambda _l: None,
        mask=ap.make_masker([]),
    )
    ap.write_slot_env(ctx, 3, "tok-abc", "https://api.example", user="bm-3")
    sent = " ".join(transport.commands)
    assert "GROK_HOME=/opt/buildmill/agents/3/grok" in sent
    # a stale login session outranks the API key and would take the run
    # off-meter — asserted on every provision, not only at install
    assert "rm -f /opt/buildmill/agents/3/grok/auth.json" in sent


def test_buildmill_module_installs_the_same_package_as_claude():
    assert ap.MODULE_NPM_PACKAGES["buildmill"] == ap.MODULE_NPM_PACKAGES["claude"]
    assert ap.MODULE_BINARIES["buildmill"] == ap.MODULE_BINARIES["claude"]


def test_install_modules_installs_grok_from_its_own_release_not_npm():
    transport = FakeTransport()
    ctx = ap.StepCtx(
        transport=transport,
        host={"workdir": "/opt/buildmill", "modules": ["grok", "opencode"]},
        password=None,
        log=lambda _l: None,
        mask=ap.make_masker([]),
    )
    ap.install_modules(ctx)
    grok_cmd = next(c for c in transport.commands if "grok-cli/main/install.sh" in c)
    assert "npm install" not in grok_cmd
    # A copy, not a symlink: /root is 0700, so a symlink there is unreadable
    # by the `buildmill` service user the agent actually runs as. Removed
    # first so a leftover symlink from a pre-fix host doesn't make `cp`
    # refuse itself ("are the same file").
    assert "rm -f /usr/local/bin/grok" in grok_cmd
    assert "cp -f /root/.grok/bin/grok /usr/local/bin/grok" in grok_cmd
    assert "ln -s" not in grok_cmd
    opencode_cmd = next(c for c in transport.commands if "opencode-ai" in c)
    assert "npm install -g" in opencode_cmd


# ---------------------------------------------------------------------------
# US-83.1: a pinned CLI with its config doors closed
# ---------------------------------------------------------------------------


def test_slot_env_disables_the_interactive_clis_auto_updater():
    """The CLI's auto-updater defaults ON; a fleet binary that can move
    mid-run is unmeasurable. The docs provide this variable for CI/containers,
    which is exactly what a pool slot is."""
    transport = FakeTransport()
    ctx = ap.StepCtx(
        transport=transport,
        host={"workdir": "/opt/buildmill", "modules": []},
        password=None,
        log=lambda _l: None,
        mask=ap.make_masker([]),
    )
    ap.write_slot_env(ctx, 3, "tok-abc", "https://api.example")
    assert "GROK_DISABLE_AUTOUPDATER=1" in " ".join(transport.commands)


def test_the_interactive_install_pins_its_version():
    """The installer takes a version as its first positional argument
    (measured on Pod-001); without it, every provision takes whatever is
    latest that day."""
    assert f"bash -s -- {ap.INTERACTIVE_CLI_VERSION}" in ap.INTERACTIVE_CLI_INSTALL_CMD


def _interactive_ctx(responder=None):
    transport = FakeTransport(responder)
    return transport, ap.StepCtx(
        transport=transport,
        host={"workdir": "/opt/buildmill", "modules": ["interactive"]},
        password=None,
        log=lambda _l: None,
        mask=ap.make_masker([]),
    )


def test_an_interactive_cli_reporting_the_pin_passes():
    def responder(command: str):
        if "--version" in command:
            return (0, f"grok {ap.INTERACTIVE_CLI_VERSION} (3cd0d0cbce) [stable]")
        return (0, "")

    _transport, ctx = _interactive_ctx(responder)
    ap.install_modules(ctx)  # must not raise
    assert ap.INTERACTIVE_CLI_VERSION in ctx.versions["interactive"]


def test_an_interactive_cli_off_the_pin_fails_the_provision():
    """A mismatch must be loud: a host on the wrong build would drift from
    every measurement the module was written against."""

    def responder(command: str):
        if "--version" in command:
            return (0, "grok 9.9.9 (feedface) [stable]")
        return (0, "")

    _transport, ctx = _interactive_ctx(responder)
    with pytest.raises(ap.JobError) as caught:
        ap.install_modules(ctx)
    assert ap.INTERACTIVE_CLI_VERSION in str(caught.value)
    assert "9.9.9" in str(caught.value)


def test_an_interactive_cli_that_cannot_state_its_version_fails_too():
    """'unknown' is the SILENTLY SKIPPED failure mode this file already
    documents — it must not read as a pass."""
    _transport, ctx = _interactive_ctx()
    with pytest.raises(ap.JobError):
        ap.install_modules(ctx)


# ---------------------------------------------------------------------------
# Pool placement queue (US-57.3 follow-on, 2026-07-31)
# ---------------------------------------------------------------------------


class _FakeSettings:
    database_url = "postgres://fake"


PENDING_REQUEST = {
    "id": "req-1",
    "org_id": "org-1",
    "pool_id": "pool-1",
    "worker_id": "worker-1",
    "requested_by": "user-1",
    "requested_by_email": "user@example.com",
}

POOL_ROW = {
    "id": "pool-1",
    "org_id": "platform-org",
    "status": "ready",
    "shared": True,
    "pool_name": "Alpha",
    "capacity": 4,
}


def test_pool_placement_sweep_places_a_request_once_the_host_frees_up(monkeypatch):
    launched = {}
    deleted = []

    monkeypatch.setattr(ap, "due_pool_placements", lambda settings, limit=5: [dict(PENDING_REQUEST)])
    monkeypatch.setattr(ap, "_worker_already_placed", lambda settings, worker_id: False)
    monkeypatch.setattr(ap, "_placement_pool_row", lambda settings, pool_id: dict(POOL_ROW))
    monkeypatch.setattr(ap, "_placement_free_slots", lambda settings, pool_id, capacity: 1)
    monkeypatch.setattr(ap, "create_job", lambda settings, **kwargs: {"id": "job-1", **kwargs})
    monkeypatch.setattr(ap, "delete_pool_placement", lambda settings, request_id: deleted.append(request_id))
    monkeypatch.setattr(ap, "launch", lambda settings, ctx: launched.update(ctx))

    import asyncio

    placed = asyncio.run(ap.pool_placement_sweep(_FakeSettings()))

    assert placed == 1
    assert deleted == ["req-1"]
    assert launched["adopt_worker_id"] == "worker-1"
    assert launched["agent_server_id"] == "pool-1"


def test_pool_placement_sweep_leaves_a_still_busy_request_queued(monkeypatch):
    def busy(settings, **kwargs):
        raise ap.JobActive("This machine already has a job running.")

    calls = {"deleted": False, "failed": False}

    monkeypatch.setattr(ap, "due_pool_placements", lambda settings, limit=5: [dict(PENDING_REQUEST)])
    monkeypatch.setattr(ap, "_worker_already_placed", lambda settings, worker_id: False)
    monkeypatch.setattr(ap, "_placement_pool_row", lambda settings, pool_id: dict(POOL_ROW))
    monkeypatch.setattr(ap, "_placement_free_slots", lambda settings, pool_id, capacity: 1)
    monkeypatch.setattr(ap, "create_job", busy)
    monkeypatch.setattr(ap, "delete_pool_placement", lambda settings, request_id: calls.__setitem__("deleted", True))
    monkeypatch.setattr(ap, "fail_pool_placement", lambda settings, request_id, error: calls.__setitem__("failed", True))
    monkeypatch.setattr(ap, "launch", lambda settings, ctx: pytest.fail("should not launch while busy"))

    import asyncio

    placed = asyncio.run(ap.pool_placement_sweep(_FakeSettings()))

    assert placed == 0
    assert calls == {"deleted": False, "failed": False}


def test_pool_placement_sweep_drops_a_request_already_placed_elsewhere(monkeypatch):
    """Two placement attempts for the same worker race; by the time the
    sweep runs one already succeeded — the queued duplicate should just be
    cleared, not launched a second time."""
    deleted = []

    monkeypatch.setattr(ap, "due_pool_placements", lambda settings, limit=5: [dict(PENDING_REQUEST)])
    monkeypatch.setattr(ap, "_worker_already_placed", lambda settings, worker_id: True)
    monkeypatch.setattr(ap, "delete_pool_placement", lambda settings, request_id: deleted.append(request_id))
    monkeypatch.setattr(ap, "create_job", lambda settings, **kwargs: pytest.fail("should not create a job"))
    monkeypatch.setattr(ap, "launch", lambda settings, ctx: pytest.fail("should not launch"))

    import asyncio

    placed = asyncio.run(ap.pool_placement_sweep(_FakeSettings()))

    assert placed == 0
    assert deleted == ["req-1"]


def test_pool_placement_sweep_fails_a_request_whose_pool_filled_up(monkeypatch):
    failed = {}

    monkeypatch.setattr(ap, "due_pool_placements", lambda settings, limit=5: [dict(PENDING_REQUEST)])
    monkeypatch.setattr(ap, "_worker_already_placed", lambda settings, worker_id: False)
    monkeypatch.setattr(ap, "_placement_pool_row", lambda settings, pool_id: dict(POOL_ROW))
    monkeypatch.setattr(ap, "_placement_free_slots", lambda settings, pool_id, capacity: 0)
    monkeypatch.setattr(ap, "fail_pool_placement", lambda settings, request_id, error: failed.update(id=request_id, error=error))
    monkeypatch.setattr(ap, "create_job", lambda settings, **kwargs: pytest.fail("should not create a job"))
    monkeypatch.setattr(ap, "launch", lambda settings, ctx: pytest.fail("should not launch"))

    import asyncio

    placed = asyncio.run(ap.pool_placement_sweep(_FakeSettings()))

    assert placed == 0
    assert failed["id"] == "req-1"
    assert "Alpha" in failed["error"]
