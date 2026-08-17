"""Agent server provisioning over SSH (Phase 26, US-26.1–26.10).

An *agent server* is a machine an admin registers so Build Mill can install,
run, update and retire coding agents on it. Everything in this module runs
server-side over the SSH bridge `api` already owns (`ssh.py`, reused through
`deploy.py`'s connect/exec helpers): the browser never holds a credential and
never talks to the machine.

Nothing here is a new runner architecture. The supervisor
(`apps/runner/supervisor`) was already built to be dropped on any machine with
Python and a worker token, configured entirely server-side. This module is the
act of dropping it there, plus the fleet management that follows from owning
the box.

Three invariants worth keeping in mind when editing:

* **One secret on the machine.** Each slot gets one worker token in a 0600 env
  file. No model key (the LLM gateway mints scoped keys per run) and no GitHub
  credential (the supervisor clones through the factory git proxy). Never add a
  third.
* **The log is public, the credentials are not.** `agent_server_jobs.log` is
  readable by any org member; every line goes through the masker before it is
  stored, and sudo passwords go over stdin, never on a command line.
* **Jobs are idempotent.** Every step must be safe to re-run on a machine where
  it already succeeded — that is what makes a failed provision resumable
  instead of a half-installed box someone has to clean by hand.

Writes here use direct Postgres (service role equivalent) because a job
outlives the request that started it. Authorization happened in the router.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import os
import posixpath
import re
import secrets
import shlex
import tarfile
import tempfile
import time
from typing import Any, Callable

import paramiko
import psycopg

from . import db, deploy, ssh
from .config import Settings
from .pool import pool_for

logger = logging.getLogger("uvicorn.error")

# Keep strong references to in-flight job tasks (asyncio only holds weak ones).
_TASKS: set[asyncio.Task] = set()

SERVICE_USER = "buildmill"
UNIT_TEMPLATE_NAME = "buildmill-agent@.service"
NODE_MAJOR = 20

# How long a drain waits for an in-flight run before giving up on a slot. The
# slot is then skipped and named — killing a coding run to install a patch
# trades real work for a version number (US-26.8).
DRAIN_CEILING_SECONDS = 600
# How long provisioning waits for a freshly started service to open its
# control socket before calling it a failure (US-26.4).
CONNECT_WAIT_SECONDS = 90
# Capacity advice, not a rule (US-26.7).
MIN_FREE_GB_FOR_SLOT = 10
# A probe older than this is shown as stale rather than as current data.
PROBE_STALE_SECONDS = 900

BASE_PACKAGES = (
    "git",
    "curl",
    "ca-certificates",
    "tar",
    "python3",
    "python3-venv",
    "python3-pip",
    # US-2.8.1 postmortem (2026-08-12): a minimal Debian image ships dash as
    # sh and no bash at all, but agent CLIs shell out via `/usr/bin/bash -lc`
    # — on pool machine 9 every terminal command of a 26-minute plan run
    # failed on this. Debian's bash package lands at /bin/bash with the
    # usr-merge alias at /usr/bin/bash, which covers the hardcoded path too.
    "bash",
)

# npm package -> the binary each supervisor module shells out to. A host whose
# CLI ships under a different name overrides it with setup_commands rather than
# waiting for a code change (US-26.3).
#
# `grok` is NOT installed from npm. `@vibe-kit/grok-cli` (npm, frozen at 0.0.34)
# is an unrelated community wrapper — it answers to GROK_API_KEY, has no
# --output-format/--always-approve flags, and hits a live xAI 410 on every real
# call ("Live search is deprecated"). The CLI Build Mill's Grok Build module was
# actually written against is `superagent-ai/grok-cli`'s own standalone binary
# (self-identifies as "Grok Build TUI") — installed via its release script, not
# npm. Its own interface changed completely between 1.0.0 and the 1.1.7 its
# release channel resolves to today; see supervisor/modules/grok.py for the
# CLI flags actually measured against 1.1.7.
MODULE_NPM_PACKAGES = {
    "claude": "@anthropic-ai/claude-code",
    # US-60.1: Buildmill Agent is Claude Code under a platform-billed name —
    # same package, same binary, so it installs and runs identically.
    "buildmill": "@anthropic-ai/claude-code",
    "opencode": "opencode-ai",
}
MODULE_BINARIES = {
    "claude": "claude",
    "buildmill": "claude",
    "grok": "grok",
    "opencode": "opencode",
    # US-78.1: deliberately NOT `grok`. The Grok Build module above installs
    # superagent-ai/grok-cli under that name and both live on every pool
    # machine — sharing the name would be a coin flip over which agent answers.
    "interactive": "buildmill-agent-cli",
}
# `sim` is the built-in simulated module: no CLI, nothing to install.
#
# US-78.1: install strategy is declared per module rather than inferred from
# membership of MODULE_NPM_PACKAGES. `install_modules` used to filter on
# `m in MODULE_NPM_PACKAGES or m == "grok"`, so a module in neither was
# SILENTLY SKIPPED: provisioning reported success, the version probe read
# "unknown", and the first run failed with a command-not-found that looked
# like a runner bug. A module with no strategy is now a loud failure.
MODULE_INSTALL_SCRIPTS = {"grok", "interactive"}
KNOWN_MODULES = set(MODULE_NPM_PACKAGES) | MODULE_INSTALL_SCRIPTS | {"sim"}

# The GitHub release asset the install script itself would fetch for a
# linux-x64 agent server — every agent server this platform provisions onto is
# Linux (BASE_PACKAGES uses apt), so there is only ever one target to resolve.
#
# `npm uninstall -g @vibe-kit/grok-cli` first: a host provisioned before this
# fix has the wrong package's shim at npm's global bin path, which also
# answers to `grok` — left in place, it would race the copy below for
# whichever comes first on PATH. `|| true` because a host that never had it
# must not fail this step.
#
# `cp`, not `ln -sf`, into /usr/local/bin: the install script runs as root and
# writes the binary under /root/.grok/bin, and /root is 0700 — a symlink there
# is unreadable by the `buildmill` service user the agent actually runs as
# (and by anyone else), so the CLI would exist but never execute. Copying the
# bytes out to a world-traversable path sidesteps that permission wall
# entirely. Measured live: the symlink version left every agent reporting
# "grok: unknown" and every run unable to invoke it.
#
# `rm -f` the destination before `cp`: a host that already ran the symlink
# version of this step has `/usr/local/bin/grok` as a symlink INTO
# `/root/.grok/bin/grok` — `cp -f` alone refuses that ("are the same file")
# because it resolves the symlink before comparing. Idempotent either way:
# a plain file there is removed and replaced same as a symlink would be.
GROK_CLI_INSTALL_CMD = (
    "npm uninstall -g @vibe-kit/grok-cli >/dev/null 2>&1 || true"
    " && curl -fsSL https://raw.githubusercontent.com/superagent-ai/grok-cli/main/install.sh"
    " | bash -s -- --no-modify-path"
    " && rm -f /usr/local/bin/grok"
    " && cp -f /root/.grok/bin/grok /usr/local/bin/grok"
    " && chmod 755 /usr/local/bin/grok"
)

# US-78.1: the Buildmill Interactive Agent's CLI — a fork of xai-org/grok-build.
#
# **This command is written against what the installer actually did on Pod-001
# on 2026-08-11, not against its documentation.** Two assumptions carried over
# from GROK_CLI_INSTALL_CMD were both wrong, and the second one broke a live
# machine:
#
#   1. `bash -s -- --no-modify-path` is superagent-ai's flag. THIS installer
#      takes a VERSION as its first positional argument, so it read the flag as
#      one and died with "Invalid version format: --no-modify-path".
#
#   2. Far worse: the installer **symlinks `/usr/local/bin/grok` and
#      `/usr/local/bin/agent` unconditionally**, regardless of HOME or
#      GROK_HOME. On a host that also runs the Grok Build module, that replaces
#      superagent-ai's 1.1.7 binary with xAI's 1.0.0 — a different program with
#      a different interface — and every existing Grok Build agent silently
#      starts invoking the wrong CLI. Measured live: a 117MB regular file became
#      a symlink, and three working agents were broken by one Update.
#
# So the two symlinks it plants are removed afterwards, and `/usr/local/bin/grok`
# is RESTORED from superagent-ai's own install root when that host has one. The
# restore is conditional: a host without the Grok Build module should simply not
# have a `grok` on its PATH, rather than xAI's under that name.
#
# Everything else is the lesson GROK_CLI_INSTALL_CMD already paid for: copy the
# bytes to a world-traversable path (never symlink into a 0700 home), `rm -f`
# the destination first because `cp -f` refuses a symlink resolving to its own
# source, and land under `buildmill-agent-cli` so the two CLIs never share a
# name.
#
# US-83.1: the fleet runs THIS version, not whatever the installer resolves the
# day a machine happens to provision. The installer takes a version as its
# first positional argument (measured — see assumption 1 above), and the verify
# step below fails the provision when the installed binary reports anything
# else. Upgrades are a deliberate change to this constant, never a side effect.
INTERACTIVE_CLI_VERSION = "1.0.0"

INTERACTIVE_CLI_INSTALL_CMD = (
    "mkdir -p /opt/buildmill-agent-cli"
    " && env HOME=/opt/buildmill-agent-cli bash -c"
    f" 'curl -fsSL https://x.ai/cli/install.sh | bash -s -- {INTERACTIVE_CLI_VERSION}'"
    # undo the installer's unconditional symlinks before anything else can run
    " && rm -f /usr/local/bin/agent /usr/local/bin/grok"
    " && if [ -x /root/.grok/bin/grok ]; then"
    " cp -f /root/.grok/bin/grok /usr/local/bin/grok"
    " && chmod 755 /usr/local/bin/grok; fi"
    " && rm -f /usr/local/bin/buildmill-agent-cli"
    " && cp -f /opt/buildmill-agent-cli/.grok/bin/grok /usr/local/bin/buildmill-agent-cli"
    " && chmod 755 /usr/local/bin/buildmill-agent-cli"
)

MODULE_INSTALL_COMMANDS = {
    "grok": GROK_CLI_INSTALL_CMD,
    "interactive": INTERACTIVE_CLI_INSTALL_CMD,
}


class JobError(Exception):
    """A step failed. Carries the step name so the row can say where."""

    def __init__(self, step: str, message: str):
        super().__init__(f"{step}: {message}")
        self.step = step
        self.message = message


class JobActive(Exception):
    """Single-flight: this host already has a queued/running job."""


def _connect(settings: Settings):
    """US-87.6: leased from the process-wide pool (app/pool.py), not a new
    connection per call. Shares one pool with db.py so the whole API has a
    single, bounded connection budget."""
    return pool_for(settings).connection()


# ---------------------------------------------------------------------------
# Redaction — the log is readable by any org member
# ---------------------------------------------------------------------------


def make_masker(values: list[str | None]) -> Callable[[str], str]:
    """Replace every known secret with a marker, longest first.

    Longest-first matters: a passphrase that is a prefix of a token would
    otherwise leave the token's tail in the log.
    """
    real = sorted({v for v in values if v and len(v) >= 4}, key=len, reverse=True)

    def mask(line: str) -> str:
        for v in real:
            if v in line:
                line = line.replace(v, "••••••")
        # belt and braces: any sfw_ token that reached the output some other
        # way (a service printing its own config, say) never gets stored.
        return re.sub(r"sfw_[0-9a-f]{8,}", "sfw_••••••", line)

    return mask


# ---------------------------------------------------------------------------
# The bundle — apps/runner, pushed from this API host
# ---------------------------------------------------------------------------

_BUNDLE_EXCLUDE_DIRS = {"__pycache__", ".venv", "venv", "workspace", ".pytest_cache"}
_BUNDLE_EXCLUDE_SUFFIXES = (".pyc", ".pyo", ".log")


def runner_source_dir() -> str:
    """`apps/runner` in this checkout — the API and the agents ship together."""
    here = os.path.dirname(os.path.abspath(__file__))          # apps/api/app
    return os.path.normpath(os.path.join(here, "..", "..", "runner"))


def _bundle_files(root: str) -> list[tuple[str, str]]:
    """(absolute path, posix relative path) for everything in the bundle."""
    out: list[tuple[str, str]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _BUNDLE_EXCLUDE_DIRS)
        for name in sorted(filenames):
            if name.endswith(_BUNDLE_EXCLUDE_SUFFIXES):
                continue
            abs_path = os.path.join(dirpath, name)
            rel = os.path.relpath(abs_path, root).replace(os.sep, "/")
            out.append((abs_path, rel))
    return sorted(out, key=lambda p: p[1])


def bundle_hash(root: str | None = None) -> str:
    """Content hash of the runner tree — this is the agent version.

    Hashing content (sorted path + bytes) rather than the tar keeps it stable
    across machines and checkouts: two deploys of the same commit produce the
    same hash, so "is this host stale" is a real comparison and not a race
    against file mtimes. There is no version file to bump, so the recorded
    version and the installed code cannot disagree (US-26.2 / US-26.8).
    """
    root = root or runner_source_dir()
    digest = hashlib.sha256()
    for abs_path, rel in _bundle_files(root):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        with open(abs_path, "rb") as fh:
            digest.update(fh.read())
        digest.update(b"\0")
    return digest.hexdigest()[:16]


def build_bundle(root: str | None = None) -> tuple[str, str]:
    """Write a deterministic tar.gz of the runner tree. Returns (path, hash)."""
    root = root or runner_source_dir()
    digest = bundle_hash(root)
    fd, path = tempfile.mkstemp(prefix=f"buildmill-bundle-{digest}-", suffix=".tar.gz")
    os.close(fd)
    with tarfile.open(path, "w:gz") as tar:
        for abs_path, rel in _bundle_files(root):
            info = tar.gettarinfo(abs_path, arcname=rel)
            # fixed metadata so the archive is reproducible
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            with open(abs_path, "rb") as fh:
                tar.addfile(info, fh)
    return path, digest


# ---------------------------------------------------------------------------
# Shell helpers
# ---------------------------------------------------------------------------


def sudo_wrap(command: str, *, have_password: bool) -> str:
    """Wrap a command for sudo.

    With a stored password we feed it on stdin (`-S`, empty prompt) — it never
    appears on a command line, where it would show up in `ps` on the target and
    in any log that echoes the command. With key auth we require passwordless
    sudo (`-n`) and let preflight explain if it is missing.
    """
    inner = shlex.quote(command)
    flag = "-S -p ''" if have_password else "-n"
    return f"sudo {flag} -- sh -c {inner}"


def run(
    transport: paramiko.Transport,
    command: str,
    *,
    sudo: bool = False,
    password: str | None = None,
    log: Callable[[str], None] | None = None,
    mask: Callable[[str], str] | None = None,
    echo: bool = True,
) -> tuple[int, list[str]]:
    """Run one command, streaming masked output into the job log."""
    real = sudo_wrap(command, have_password=bool(password)) if sudo else command
    stdin = f"{password}\n".encode() if (sudo and password) else None
    lines: list[str] = []

    if log and echo:
        shown = mask(command) if mask else command
        log(f"$ {shown}")

    def capture(line: str) -> None:
        clean = mask(line) if mask else line
        lines.append(clean)
        if log:
            log(clean)

    status = deploy._exec(transport, real, stdin, capture)
    return status, lines


def run_ok(
    transport: paramiko.Transport,
    command: str,
    *,
    step: str,
    **kwargs: Any,
) -> list[str]:
    """`run`, but a non-zero exit is a JobError naming the step."""
    status, lines = run(transport, command, **kwargs)
    if status != 0:
        tail = "; ".join(lines[-3:]) or f"exit {status}"
        raise JobError(step, tail)
    return lines


def quiet(transport: paramiko.Transport, command: str, **kwargs: Any) -> tuple[int, list[str]]:
    """A read-only probe command: no log echo, output captured."""
    return run(transport, command, echo=False, **kwargs)


# ---------------------------------------------------------------------------
# Preflight (US-26.1)
# ---------------------------------------------------------------------------


def api_url_problem(api_url: str) -> str | None:
    """US-27.13: why this API address cannot be written into a slot's env, or
    None when it can be.

    The first agent server provisioned cleanly and produced two agents that
    could never work: every slot was told `FACTORY_API_URL=http://localhost:8000`
    — the API's own default, because `API_BASE_URL` was never set in the
    deployed `.env`. Both supervisors dialled themselves and looped on
    `Connect call failed ('127.0.0.1', 8000)`. No remote machine can reach the
    factory on its own loopback, so this is refused without even testing it."""
    url = (api_url or "").strip()
    if not url:
        return (
            "the factory's own address is not configured — set API_BASE_URL in "
            "apps/api/.env on the API host to the URL agents should dial "
            "(e.g. https://api.buildmill.dev)"
        )
    host = url.split("//", 1)[-1].split("/", 1)[0].split(":", 1)[0].lower()
    if host in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}:
        return (
            f"the factory's address is {url}, which is this API host's own "
            "loopback — no remote machine can reach it. Set API_BASE_URL in "
            "apps/api/.env to the address agents should dial "
            "(e.g. https://api.buildmill.dev)"
        )
    return None


def connect_timeout_message(index: int, api_url: str) -> str:
    """US-26.4 / US-27.13: a slot whose service started but never dialled home
    is a first-class failure, and the message names the URL it was told to
    dial. Without that, the failure reads as "the agent is broken" when the
    actual fact is that the address it was handed does not resolve from this
    machine — which is exactly how two agents were installed and left
    unusable on 2026-07-26."""
    return (
        f"[slot {index} started but never opened its control socket within "
        f"{CONNECT_WAIT_SECONDS}s. It was told to dial {api_url} — "
        "if that is not an address THIS MACHINE can reach, set API_BASE_URL in the "
        "API's .env and run Update, which re-points every slot]"
    )


def preflight(
    transport: paramiko.Transport,
    workdir: str,
    password: str | None,
    api_url: str = "",
) -> list[dict[str, Any]]:
    """Named checks, run before anything is installed.

    A machine that fails any of these is rejected here — not accepted and left
    to fail three minutes into an install with half a toolchain on it.
    """
    checks: list[dict[str, Any]] = [
        {"check": "ssh", "ok": True, "detail": "Connected and authenticated"}
    ]

    status, lines = quiet(transport, "cat /etc/os-release")
    os_id, id_like, pretty = "", "", ""
    for line in lines:
        if line.startswith("ID="):
            os_id = line.split("=", 1)[1].strip().strip('"')
        elif line.startswith("ID_LIKE="):
            id_like = line.split("=", 1)[1].strip().strip('"')
        elif line.startswith("PRETTY_NAME="):
            pretty = line.split("=", 1)[1].strip().strip('"')
    debian = os_id in {"debian", "ubuntu"} or "debian" in id_like
    checks.append(
        {
            "check": "os",
            "ok": status == 0 and debian,
            "detail": (
                f"{pretty or os_id}"
                if debian
                else f"not a Debian-family machine (ID={os_id or 'unknown'}) — "
                "agent servers are Ubuntu/Debian with systemd"
            ),
        }
    )

    status, _ = quiet(transport, "command -v systemctl >/dev/null 2>&1")
    checks.append(
        {
            "check": "systemd",
            "ok": status == 0,
            "detail": "systemd is present" if status == 0 else "no systemd on this machine",
        }
    )

    status, lines = quiet(
        transport, "id -u", sudo=True, password=password
    )
    checks.append(
        {
            "check": "sudo",
            "ok": status == 0,
            "detail": (
                "the SSH user can sudo"
                if status == 0
                else "the SSH user cannot sudo — provisioning installs packages "
                "and writes systemd units, so it needs root"
            ),
        }
    )

    status, lines = quiet(transport, "uname -m")
    arch = lines[0].strip() if lines else ""
    checks.append(
        {
            "check": "arch",
            "ok": arch in {"x86_64", "aarch64"},
            "detail": arch or "could not read the architecture",
        }
    )

    parent = posixpath.dirname(workdir.rstrip("/")) or "/"
    status, lines = quiet(
        transport, f"df -Pk {shlex.quote(parent)} | awk 'NR==2 {{print $4}}'"
    )
    free_gb = round(int(lines[0]) / 1_048_576, 1) if status == 0 and lines and lines[0].strip().isdigit() else None
    checks.append(
        {
            "check": "disk",
            "ok": free_gb is not None and free_gb >= 2,
            "detail": (
                f"{free_gb} GB free on {parent}"
                if free_gb is not None
                else "could not read free disk space"
            ),
        }
    )

    # US-27.13: the check the first provisioning run needed. Every other check
    # here asks something about the machine; this one asks whether the machine
    # can reach US. It is trivially reachable from the API host — which is
    # exactly what made shipping a loopback address so easy — so it is tested
    # from the far end of the SSH session, where it matters.
    checks.append(_factory_reachable(transport, api_url))
    return checks


def _factory_reachable(
    transport: paramiko.Transport, api_url: str
) -> dict[str, Any]:
    problem = api_url_problem(api_url)
    if problem:
        return {"check": "factory-reachable", "ok": False, "detail": problem}
    health = api_url.rstrip("/") + "/api/v1/health"
    status, lines = quiet(
        transport,
        # -sS keeps curl quiet but still prints why it failed; the write-out
        # is the whole answer, so a machine with no curl reads as a curl
        # problem rather than as an unreachable factory.
        f"curl -sS -m 10 -o /dev/null -w '%{{http_code}}' {shlex.quote(health)}",
    )
    code = (lines[0].strip() if lines else "")
    if status != 0 or not code.startswith("2"):
        return {
            "check": "factory-reachable",
            "ok": False,
            "detail": (
                f"this machine could not reach {health} (curl said "
                f"{code or 'nothing'}). Agents dial that address on every "
                "poll — check DNS, firewalls, and that API_BASE_URL names an "
                "address reachable from here"
            ),
        }
    return {
        "check": "factory-reachable",
        "ok": True,
        "detail": f"reached {health} from this machine (HTTP {code})",
    }


# ---------------------------------------------------------------------------
# Install steps (each one safe to re-run)
# ---------------------------------------------------------------------------


def _q(path: str) -> str:
    return shlex.quote(path)


def install_base(ctx: "StepCtx") -> None:
    packages = " ".join(BASE_PACKAGES)
    run_ok(
        ctx.transport,
        "DEBIAN_FRONTEND=noninteractive apt-get update -qq",
        step="base packages",
        sudo=True,
        password=ctx.password,
        log=ctx.log,
        mask=ctx.mask,
    )
    run_ok(
        ctx.transport,
        f"DEBIAN_FRONTEND=noninteractive apt-get install -y -qq {packages}",
        step="base packages",
        sudo=True,
        password=ctx.password,
        log=ctx.log,
        mask=ctx.mask,
    )

    status, _ = quiet(ctx.transport, "command -v node >/dev/null 2>&1")
    if status != 0:
        ctx.log(f"[installing Node {NODE_MAJOR} from NodeSource]")
        run_ok(
            ctx.transport,
            f"curl -fsSL https://deb.nodesource.com/setup_{NODE_MAJOR}.x | bash -",
            step="node",
            sudo=True,
            password=ctx.password,
            log=ctx.log,
            mask=ctx.mask,
        )
        run_ok(
            ctx.transport,
            "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nodejs",
            step="node",
            sudo=True,
            password=ctx.password,
            log=ctx.log,
            mask=ctx.mask,
        )
    else:
        ctx.log("[node already installed]")


def install_service_user(ctx: "StepCtx") -> None:
    """The dedicated non-login account the agents run as."""
    run_ok(
        ctx.transport,
        f"id -u {SERVICE_USER} >/dev/null 2>&1 || "
        f"useradd --system --create-home --shell /usr/sbin/nologin {SERVICE_USER}",
        step="service user",
        sudo=True,
        password=ctx.password,
        log=ctx.log,
        mask=ctx.mask,
    )
    workdir = ctx.host["workdir"]
    run_ok(
        ctx.transport,
        f"mkdir -p {_q(workdir)}/app {_q(workdir)}/env {_q(workdir)}/agents"
        # US-57.4: `agents/` deliberately NOT swept here — on a shared host
        # every slot below it is chowned to its own per-slot user
        # (write_slot_env), and this step re-runs on every provision AND
        # update (_job_update); a recursive chown of the whole workdir would
        # silently revert that back to SERVICE_USER on the next Update.
        f" && chown {SERVICE_USER}:{SERVICE_USER} {_q(workdir)}"
        f" && chown -R {SERVICE_USER}:{SERVICE_USER} {_q(workdir)}/app {_q(workdir)}/env"
        f" && chmod 750 {_q(workdir)}/env",
        step="service user",
        sudo=True,
        password=ctx.password,
        log=ctx.log,
        mask=ctx.mask,
    )

    sudoers = f"/etc/sudoers.d/buildmill-agent"
    if ctx.host.get("allow_agent_sudo"):
        ctx.log("[allow_agent_sudo is ON — writing a sudoers drop-in for the agent user]")
        run_ok(
            ctx.transport,
            f"printf '%s\\n' '{SERVICE_USER} ALL=(ALL) NOPASSWD: ALL' > {sudoers}"
            f" && chmod 440 {sudoers}",
            step="agent sudo",
            sudo=True,
            password=ctx.password,
            log=ctx.log,
            mask=ctx.mask,
        )
    else:
        # off by default, and turning it off takes the grant away again
        run(
            ctx.transport,
            f"rm -f {sudoers}",
            sudo=True,
            password=ctx.password,
            log=ctx.log,
            mask=ctx.mask,
        )


def install_modules(ctx: "StepCtx") -> None:
    """Coding-agent CLIs, and the versions they turned out to be (US-26.3)."""
    requested = [m for m in (ctx.host.get("modules") or []) if m != "sim"]
    # US-78.1: a module with no declared install strategy used to be filtered
    # out here and never mentioned again — provisioning reported success and
    # the CLI was simply absent. Say so instead.
    unknown = [m for m in requested if m not in KNOWN_MODULES]
    if unknown:
        raise JobError(
            "agent CLIs",
            "no install strategy for "
            + ", ".join(sorted(unknown))
            + " — add it to MODULE_NPM_PACKAGES or MODULE_INSTALL_COMMANDS",
        )
    modules = [m for m in requested if m in MODULE_NPM_PACKAGES or m in MODULE_INSTALL_COMMANDS]
    if not modules:
        ctx.log("[no agent CLIs selected — this host can run the supervisor and nothing else]")
        return
    for module in modules:
        if module in MODULE_INSTALL_COMMANDS:
            run_ok(
                ctx.transport,
                MODULE_INSTALL_COMMANDS[module],
                step=f"agent CLI {module}",
                sudo=True,
                password=ctx.password,
                log=ctx.log,
                mask=ctx.mask,
            )
            continue
        package = MODULE_NPM_PACKAGES[module]
        run_ok(
            ctx.transport,
            f"npm install -g --no-fund --no-audit {shlex.quote(package)}",
            step=f"agent CLI {module}",
            sudo=True,
            password=ctx.password,
            log=ctx.log,
            mask=ctx.mask,
        )
    ctx.versions.update(read_cli_versions(ctx.transport, modules))
    for module, version in ctx.versions.items():
        ctx.log(f"[{module}: {version}]")
    # US-83.1: the pin is only real if a mismatch is loud. "unknown" fails too —
    # a binary that cannot state its version is the SILENTLY SKIPPED failure
    # mode this file already documents, not a pass.
    if "interactive" in modules:
        reported = ctx.versions.get("interactive", "unknown")
        if INTERACTIVE_CLI_VERSION not in reported:
            raise JobError(
                "agent CLI interactive",
                f"the installed CLI reports '{reported}' but the fleet is "
                f"pinned to {INTERACTIVE_CLI_VERSION} — the installer resolved "
                "a different build, so this host would drift from every "
                "measurement the modules were written against",
            )


def read_cli_versions(
    transport: paramiko.Transport, modules: list[str]
) -> dict[str, str]:
    out: dict[str, str] = {}
    for module in modules:
        binary = MODULE_BINARIES.get(module)
        if not binary:
            continue
        status, lines = quiet(transport, f"{shlex.quote(binary)} --version 2>/dev/null | head -1")
        out[module] = lines[0].strip() if status == 0 and lines and lines[0].strip() else "unknown"
    return out


def install_extras(ctx: "StepCtx") -> None:
    """Admin-declared packages and commands, re-applied on every run."""
    extras = [p for p in (ctx.host.get("extra_packages") or []) if p.strip()]
    if extras:
        run_ok(
            ctx.transport,
            "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "
            + " ".join(shlex.quote(p) for p in extras),
            step="extra packages",
            sudo=True,
            password=ctx.password,
            log=ctx.log,
            mask=ctx.mask,
        )
    commands = [c for c in (ctx.host.get("setup_commands") or "").splitlines() if c.strip()]
    for command in commands:
        run_ok(
            ctx.transport,
            command,
            step=f"setup command `{command.strip()[:60]}`",
            sudo=True,
            password=ctx.password,
            log=ctx.log,
            mask=ctx.mask,
        )


def push_bundle(ctx: "StepCtx") -> str:
    """Upload + extract the supervisor, build the venv. Returns the hash."""
    path, digest = build_bundle()
    workdir = ctx.host["workdir"]
    target = f"{workdir}/app/{digest}"
    remote_tmp = f"/tmp/buildmill-bundle-{digest}.tar.gz"

    status, _ = quiet(ctx.transport, f"test -d {_q(target)}")
    if status == 0 and ctx.host.get("bundle_hash") == digest:
        ctx.log(f"[bundle {digest} already installed]")
        os.unlink(path)
        return digest

    ctx.log(f"[pushing supervisor bundle {digest}]")
    try:
        deploy._upload(ctx.transport, path, remote_tmp, None)
    except Exception as e:  # noqa: BLE001 — surfaced as a step failure
        raise JobError("bundle upload", str(e))
    finally:
        os.unlink(path)

    run_ok(
        ctx.transport,
        f"mkdir -p {_q(target)} && tar -xzf {_q(remote_tmp)} -C {_q(target)}"
        f" && rm -f {_q(remote_tmp)}"
        f" && ln -sfn {_q(target)} {_q(workdir)}/app/current"
        f" && chown -R {SERVICE_USER}:{SERVICE_USER} {_q(workdir)}/app",
        step="bundle extract",
        sudo=True,
        password=ctx.password,
        log=ctx.log,
        mask=ctx.mask,
    )
    run_ok(
        ctx.transport,
        f"test -d {_q(workdir)}/venv || python3 -m venv {_q(workdir)}/venv",
        step="venv",
        sudo=True,
        password=ctx.password,
        log=ctx.log,
        mask=ctx.mask,
    )
    run_ok(
        ctx.transport,
        f"{_q(workdir)}/venv/bin/pip install -q --upgrade pip"
        f" && {_q(workdir)}/venv/bin/pip install -q -r {_q(workdir)}/app/current/requirements.txt"
        f" && chown -R {SERVICE_USER}:{SERVICE_USER} {_q(workdir)}/venv",
        step="python dependencies",
        sudo=True,
        password=ctx.password,
        log=ctx.log,
        mask=ctx.mask,
    )
    return digest


def unit_file(workdir: str) -> str:
    """One template unit; `%i` is the slot index."""
    return f"""[Unit]
Description=Build Mill agent %i
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={SERVICE_USER}
Group={SERVICE_USER}
WorkingDirectory={workdir}/app/current
EnvironmentFile={workdir}/env/%i.env
Environment=PYTHONUNBUFFERED=1
ExecStart={workdir}/venv/bin/python -m supervisor
Restart=always
RestartSec=5
KillSignal=SIGTERM
TimeoutStopSec=90

[Install]
WantedBy=multi-user.target
"""


def install_units(ctx: "StepCtx") -> None:
    workdir = ctx.host["workdir"]
    body = unit_file(workdir)
    remote = f"/etc/systemd/system/{UNIT_TEMPLATE_NAME}"
    run_ok(
        ctx.transport,
        f"cat > {_q(remote)} <<'BUILDMILL_UNIT'\n{body}BUILDMILL_UNIT\n"
        f"chmod 644 {_q(remote)} && systemctl daemon-reload",
        step="systemd unit",
        sudo=True,
        password=ctx.password,
        log=ctx.log,
        mask=ctx.mask,
    )


def slot_unix_user(index: int) -> str:
    """US-57.4: on a shared machine, each slot is its own unix account — a
    folder is only a suggestion to a process with someone else's uid."""
    return f"bm-agent-{index}"


def ensure_slot_user(ctx: "StepCtx", index: int) -> str:
    """Create the per-slot system user (with its own home) on a shared host.

    The home matters as much as the uid: a module's OAuth state, CLI config
    and caches land in `$HOME`, which systemd resolves from `User=` — so a
    tenant's Claude credential (US-57.3) is unreadable by another org's agent
    by ownership, not by convention. Only called when `host.shared`; a
    single-org host keeps running everything as `SERVICE_USER`, unchanged.
    """
    user = slot_unix_user(index)
    run_ok(
        ctx.transport,
        f"id -u {user} >/dev/null 2>&1 || "
        f"useradd --system --create-home --shell /usr/sbin/nologin {user}",
        step=f"slot {index} user",
        sudo=True,
        password=ctx.password,
        log=ctx.log,
        mask=ctx.mask,
    )
    return user


def write_slot_service_override(ctx: "StepCtx", index: int, user: str) -> None:
    """Point this one instance of the templated unit at its own user.

    `buildmill-agent@.service` stays the single shared template (every
    non-shared host still uses it exactly as Phase 26 built it) — a drop-in
    override on just this instance changes `User=`/`Group=` without forking
    the unit file or its surrounding systemctl/journalctl call sites, all of
    which keep addressing the slot as `buildmill-agent@{index}`.
    """
    override_dir = f"/etc/systemd/system/buildmill-agent@{index}.service.d"
    body = f"[Service]\nUser={user}\nGroup={user}\n"
    run_ok(
        ctx.transport,
        f"mkdir -p {_q(override_dir)}"
        f" && cat > {_q(override_dir)}/override.conf <<'BUILDMILL_OVERRIDE'\n{body}BUILDMILL_OVERRIDE\n"
        f"chmod 644 {_q(override_dir)}/override.conf && systemctl daemon-reload",
        step=f"slot {index} service user",
        sudo=True,
        password=ctx.password,
        log=ctx.log,
        mask=ctx.mask,
    )


def ensure_slot_workspace(ctx: "StepCtx", index: int, user: str | None = None) -> None:
    """Re-apply a slot's workspace ownership — idempotent and always safe to
    re-run, unlike write_slot_env's token/URL write (which US-27.13 gates
    behind a URL-match check for good reason). Before this, the only paths
    that ever touched workspace ownership were provision, add_slot, and
    reissue_token (all via write_slot_env) — so a workspace whose ownership
    drifted (its parent recreated by a different process, a manual root
    session, anything) stayed broken until the slot was removed and
    re-added. A plain Update or Restart never noticed or fixed it, and the
    runner's own reclone repair can't either — recloning into a directory it
    still can't write to fails identically. Called unconditionally on every
    slot an Update or Restart touches, so this class of drift self-heals on
    the next routine maintenance instead of requiring a token re-issue as an
    accidental workaround."""
    owner = user or SERVICE_USER
    workdir = ctx.host["workdir"]
    workspace = f"{workdir}/agents/{index}/workspace"
    run_ok(
        ctx.transport,
        f"mkdir -p {_q(workspace)}"
        f" && chown -R {owner}:{owner} {_q(workdir)}/agents/{index}"
        f" && chmod 700 {_q(workspace)}",
        step=f"slot {index} workspace",
        sudo=True,
        password=ctx.password,
        log=ctx.log,
        mask=ctx.mask,
    )


def write_slot_env(
    ctx: "StepCtx", index: int, token: str, api_url: str, *, user: str | None = None
) -> None:
    """The one secret that lands on the machine, at 0600.

    `user` is the per-slot owner on a shared host (US-57.4); `None` keeps the
    existing single-org behavior — everything owned by `SERVICE_USER`.
    """
    owner = user or SERVICE_USER
    workdir = ctx.host["workdir"]
    env_path = f"{workdir}/env/{index}.env"
    workspace = f"{workdir}/agents/{index}/workspace"
    # US-78.1: the interactive CLI keeps its config, credentials and SESSIONS
    # under GROK_HOME. On a shared pool machine the slots are different orgs'
    # agents running as different unix users, so a shared home would be a
    # cross-tenant leak of conversation history, not untidiness.
    agent_home = f"{workdir}/agents/{index}/grok"
    body = (
        f"FACTORY_API_URL={api_url}\n"
        f"FACTORY_WORKER_TOKEN={token}\n"
        f"RUNNER_WORKSPACE={workspace}\n"
        f"GROK_HOME={agent_home}\n"
        # US-83.1: the interactive CLI's auto-updater defaults ON, and a fleet
        # whose binary can move mid-run is unmeasurable — this project has been
        # bitten by CLI generation drift twice. The docs provide this variable
        # for exactly this deployment shape (CI/containers). Version changes
        # happen by re-pinning INTERACTIVE_CLI_VERSION and re-provisioning.
        "GROK_DISABLE_AUTOUPDATER=1\n"
    )
    run_ok(
        ctx.transport,
        f"mkdir -p {_q(workspace)} {_q(agent_home)}"
        f" && umask 077 && cat > {_q(env_path)} <<'BUILDMILL_ENV'\n{body}BUILDMILL_ENV\n"
        f"chmod 600 {_q(env_path)}"
        # US-78.1/78.5: a login session in auth.json OUTRANKS the API key, so a
        # stale one would send runs straight to xAI — off the gateway, unmetered,
        # billed to nobody, and looking fine from the outside. Asserted on every
        # provision rather than only at install: it is a file the CLI can write
        # for itself.
        f" && rm -f {_q(agent_home)}/auth.json"
        f" && chown {owner}:{owner} {_q(env_path)}"
        f" && chown -R {owner}:{owner} {_q(workdir)}/agents/{index}"
        f" && chmod 700 {_q(workdir)}/agents/{index}/workspace {_q(agent_home)}",
        step=f"slot {index} env",
        sudo=True,
        password=ctx.password,
        log=ctx.log,
        mask=ctx.mask,
        echo=False,  # the heredoc carries the token
    )
    ctx.log(f"[wrote {env_path} (0600, owner {owner}) — token not shown]")


def read_slot_api_url(ctx: "StepCtx", index: int) -> str | None:
    """What this slot was told to dial, read back off the machine.

    The env file is 0600 and owned by the agent user, so this needs sudo. The
    URL is not a secret — the token in the same file is, which is why only this
    one line is read.
    """
    workdir = ctx.host["workdir"]
    status, lines = quiet(
        ctx.transport,
        f"grep -h '^FACTORY_API_URL=' {_q(workdir)}/env/{index}.env 2>/dev/null",
        sudo=True,
        password=ctx.password,
    )
    if status != 0:
        return None
    for line in lines:
        if line.startswith("FACTORY_API_URL="):
            return line.split("=", 1)[1].strip()
    return None


# ---------------------------------------------------------------------------
# Probe (US-26.7)
# ---------------------------------------------------------------------------

PROBE_SCRIPT = """
echo "os=$(. /etc/os-release 2>/dev/null; echo ${PRETTY_NAME:-unknown})"
echo "cpu=$(nproc 2>/dev/null || echo 0)"
echo "mem_total=$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo)"
echo "mem_free=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)"
echo "load=$(awk '{print $1}' /proc/loadavg)"
echo "disk_total=$(df -Pk %(workdir)s 2>/dev/null | awk 'NR==2{printf "%%.2f", $2/1048576}')"
echo "disk_free=$(df -Pk %(workdir)s 2>/dev/null | awk 'NR==2{printf "%%.2f", $4/1048576}')"
echo "bundle=$(readlink %(workdir)s/app/current 2>/dev/null | xargs -r basename)"
# US-31.8: what the kept per-project workspaces cost. Persistence is the point
# of that story, so its footprint has to be a number the manager can see
# rather than a surprise when the disk fills.
echo "ws_bytes=$(du -sb %(workdir)s/agents 2>/dev/null | awk '{print $1}')"
echo "ws_count=$(find %(workdir)s/agents -maxdepth 3 -type d -name 'project-*' 2>/dev/null | wc -l)"
"""


def probe_host(
    transport: paramiko.Transport, workdir: str, slot_indexes: list[int]
) -> dict[str, Any]:
    """Read-only: never mutates the machine (US-26.7)."""
    script = PROBE_SCRIPT % {"workdir": shlex.quote(workdir)}
    status, lines = quiet(transport, script)
    if status != 0:
        raise JobError("probe", "; ".join(lines[-2:]) or f"exit {status}")

    raw: dict[str, str] = {}
    for line in lines:
        if "=" in line:
            key, _, value = line.partition("=")
            raw[key.strip()] = value.strip()

    services: dict[int, str] = {}
    for index in slot_indexes:
        _, out = quiet(transport, f"systemctl is-active buildmill-agent@{index} 2>/dev/null")
        state = (out[0].strip() if out else "") or "unknown"
        services[index] = state if state in {"active", "failed", "inactive"} else "unknown"

    return {
        "os_release": raw.get("os") or None,
        "cpu_count": _int(raw.get("cpu")),
        "mem_total_mb": _int(raw.get("mem_total")),
        "mem_free_mb": _int(raw.get("mem_free")),
        "disk_total_gb": _float(raw.get("disk_total")),
        "disk_free_gb": _float(raw.get("disk_free")),
        "load_avg": _float(raw.get("load")),
        "bundle_hash": raw.get("bundle") or None,
        # US-31.8
        "workspace_bytes": _int(raw.get("ws_bytes")),
        "workspace_count": _int(raw.get("ws_count")),
        "services": services,
    }


def _int(value: str | None) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _float(value: str | None) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Identities (US-26.4)
# ---------------------------------------------------------------------------


def mint_worker_token() -> tuple[str, str, str]:
    """Same shape the create_worker RPC mints: token, sha256 hash, last4.

    The RPC itself checks the *caller's* capability through auth.uid(), which
    is null under the service role — this path already checked manage_org in
    the router, so it writes the same rows directly.
    """
    token = "sfw_" + secrets.token_hex(24)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    return token, token_hash, token[-4:]


def create_slot_identity(
    settings: Settings, *, org_id: str, name: str, template: dict[str, Any]
) -> dict[str, Any]:
    """Principal + membership + worker + paused runner config + grants."""
    token, token_hash, last4 = mint_worker_token()
    with _connect(settings) as conn:
        row = conn.execute(
            "insert into public.principals (kind, display_name)"
            " values ('agent', %s) returning id",
            (name,),
        ).fetchone()
        principal_id = row["id"]
        conn.execute(
            "insert into public.organization_members (org_id, principal_id, role)"
            " values (%s, %s, 'agent')",
            (org_id, principal_id),
        )
        row = conn.execute(
            "insert into public.workers"
            " (org_id, name, type, token_hash, token_last4, principal_id)"
            " values (%s, %s, 'autonomous', %s, %s, %s) returning id",
            (org_id, name, token_hash, last4, principal_id),
        ).fetchone()
        worker_id = row["id"]
        conn.execute(
            "insert into public.runner_config"
            " (worker_id, org_id, enabled_modules, model_routes,"
            "  autonomy_policy, paused)"
            " values (%s, %s, %s, %s, %s, true)",
            (
                worker_id,
                org_id,
                template.get("enabled_modules") or [],
                json.dumps(template.get("model_routes") or {}),
                json.dumps(template.get("autonomy_policy") or {}),
            ),
        )
        # US-55.1: a template entry now means project ACCESS — one row per
        # project, capability = 'access'. Older templates still carrying
        # per-kind lists grant the same access; the per-kind detail is dead
        # (what an agent does is its own enabled_kinds checkboxes).
        for grant in template.get("capabilities") or []:
            project_id = grant.get("project_id")
            if not project_id:
                continue
            conn.execute(
                "insert into public.worker_capabilities"
                " (org_id, worker_id, project_id, capability)"
                " values (%s, %s, %s, 'access')"
                " on conflict (worker_id, project_id, capability) do nothing",
                (org_id, worker_id, project_id),
            )
        conn.commit()
    return {"principal_id": str(principal_id), "worker_id": str(worker_id), "token": token}


def reissue_worker_token(
    settings: Settings, worker_id: str, *, pause: bool | None = None
) -> str:
    """Mint a fresh token for an existing worker and return it.

    Tokens are hashed at rest, so a token written to a machine cannot be read
    back — re-pointing a slot at a different API URL therefore means writing a
    new token, not the old one. Any other copy of the previous token stops
    working, which is correct: for a managed slot the only copy is the env file
    being rewritten.

    `pause` leaves the runner's paused state alone when None — an update
    manages that itself and restores what the slot was.
    """
    token, token_hash, last4 = mint_worker_token()
    with _connect(settings) as conn:
        conn.execute(
            "update public.workers set token_hash = %s, token_last4 = %s,"
            " status = 'active' where id = %s",
            (token_hash, last4, worker_id),
        )
        if pause is not None:
            conn.execute(
                "insert into public.runner_config (worker_id, org_id, paused)"
                " select %s, org_id, %s from public.workers where id = %s"
                " on conflict (worker_id) do update set paused = excluded.paused",
                (worker_id, pause, worker_id),
            )
        conn.commit()
    return token


def adopt_worker_token(settings: Settings, worker_id: str) -> str:
    """Bind-an-existing-agent: re-key the worker so the new box gets a token.

    The old machine's copy stops working, which is the point — an agent moved
    to new hardware should not still be claimable from the box it left. It
    comes up paused like any new slot.
    """
    return reissue_worker_token(settings, worker_id, pause=True)


def retire_slot_identity(settings: Settings, slot: dict[str, Any]) -> None:
    """Revoke the token, suspend the membership, keep the identity.

    Past runs name the agent that did them; a roster tidied at the cost of
    history is a bad trade (US-26.9).
    """
    with _connect(settings) as conn:
        if slot.get("worker_id"):
            conn.execute(
                "update public.workers set status = 'revoked' where id = %s",
                (slot["worker_id"],),
            )
        if slot.get("principal_id"):
            conn.execute(
                "update public.organization_members set status = 'suspended'"
                " where principal_id = %s and org_id = %s",
                (slot["principal_id"], slot["org_id"]),
            )
        conn.commit()


# ---------------------------------------------------------------------------
# Pause / drain (US-26.5, US-26.8)
# ---------------------------------------------------------------------------


def set_paused(settings: Settings, worker_id: str, paused: bool) -> None:
    with _connect(settings) as conn:
        conn.execute(
            "insert into public.runner_config (worker_id, org_id, paused)"
            " select %s, org_id, %s from public.workers where id = %s"
            " on conflict (worker_id) do update set paused = excluded.paused,"
            " updated_at = now()",
            (worker_id, paused, worker_id),
        )
        conn.commit()


def worker_is_busy(settings: Settings, worker_id: str) -> dict[str, Any] | None:
    """The run a worker currently holds, if any."""
    with _connect(settings) as conn:
        return conn.execute(
            "select r.id, r.kind, r.status, i.title"
            " from public.runs r left join public.issues i on i.id = r.issue_id"
            " where r.worker_id = %s and r.status in ('claimed', 'planning', 'running')"
            " order by r.created_at desc limit 1",
            (worker_id,),
        ).fetchone()


async def drain(
    settings: Settings,
    worker_id: str,
    log: Callable[[str], None],
    ceiling: int = DRAIN_CEILING_SECONDS,
) -> bool:
    """Pause, then wait for the in-flight run to hand back.

    Returns True when the slot is idle. False means it is still busy at the
    ceiling — the caller skips it and says so rather than killing the run.
    """
    await asyncio.to_thread(set_paused, settings, worker_id, True)
    await push_config(settings, worker_id)
    deadline = time.monotonic() + ceiling
    while True:
        busy = await asyncio.to_thread(worker_is_busy, settings, worker_id)
        if not busy:
            return True
        if time.monotonic() >= deadline:
            log(f"[still running {busy.get('title') or busy['id']} after {ceiling}s — skipping]")
            return False
        log(f"[draining: waiting on {busy.get('title') or busy['id']}]")
        await asyncio.sleep(10)


async def push_config(settings: Settings, worker_id: str) -> None:
    """Push config.update to a connected supervisor, if there is one."""
    try:
        from .routers import runner_socket

        await runner_socket.push_config_update(settings, str(worker_id))
    except Exception as e:  # noqa: BLE001 — a disconnected runner is normal
        logger.debug("config push skipped for %s: %s", worker_id, e)


# ---------------------------------------------------------------------------
# Job records
# ---------------------------------------------------------------------------


def create_job(
    settings: Settings,
    *,
    org_id: str,
    agent_server_id: str,
    kind: str,
    slot_id: str | None = None,
    by: str = "",
    by_email: str = "",
) -> dict[str, Any]:
    """One job at a time per host — concurrent installs would fight."""
    with _connect(settings) as conn:
        try:
            row = conn.execute(
                "insert into public.agent_server_jobs"
                " (org_id, agent_server_id, slot_id, kind, status, started_by,"
                "  started_by_email, started_at)"
                " values (%s, %s, %s, %s, 'running', %s, %s, now()) returning *",
                (org_id, agent_server_id, slot_id, kind, by or None, by_email),
            ).fetchone()
        except psycopg.errors.UniqueViolation:
            conn.rollback()
            raise JobActive("This machine already has a job running.")
        conn.commit()
        return row


def update_job(settings: Settings, job_id: str, fields: dict[str, Any]) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k} = %s" for k in fields)
    with _connect(settings) as conn:
        conn.execute(
            f"update public.agent_server_jobs set {sets}, updated_at = now() where id = %s",
            (*fields.values(), job_id),
        )
        conn.commit()


def update_host(settings: Settings, host_id: str, fields: dict[str, Any]) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k} = %s" for k in fields)
    with _connect(settings) as conn:
        conn.execute(
            f"update public.agent_servers set {sets} where id = %s",
            (*fields.values(), host_id),
        )
        conn.commit()


def clear_host_error(settings: Settings, host_id: str) -> bool:
    """US-57.9: a host that was working, broke, and is answering again is
    `ready` — return True when this call is what moved it.

    Only `error` and `degraded` are promoted, and the guard is in the UPDATE
    rather than in a read-then-write: a host at `provisioning` has a job in
    flight whose own tail will set the status, `removed` must never be
    resurrected by a stray probe, and `new` has never worked in the first
    place so it is not recovering.
    """
    with _connect(settings) as conn:
        row = conn.execute(
            "update public.agent_servers set status = 'ready'"
            " where id = %s and status in ('error', 'degraded')"
            " returning id",
            (host_id,),
        ).fetchone()
        conn.commit()
        return row is not None


def update_slot(settings: Settings, slot_id: str, fields: dict[str, Any]) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k} = %s" for k in fields)
    with _connect(settings) as conn:
        conn.execute(
            f"update public.agent_slots set {sets} where id = %s",
            (*fields.values(), slot_id),
        )
        conn.commit()


def get_host(settings: Settings, host_id: str) -> dict[str, Any] | None:
    with _connect(settings) as conn:
        return conn.execute(
            "select a.*, s.host, s.port, s.username, s.auth_method,"
            " s.host_key_fingerprint, s.name as server_name"
            " from public.agent_servers a join public.servers s on s.id = a.server_id"
            " where a.id = %s",
            (host_id,),
        ).fetchone()


def get_slots(settings: Settings, host_id: str, live_only: bool = True) -> list[dict[str, Any]]:
    clause = " and status = 'active'" if live_only else ""
    with _connect(settings) as conn:
        return conn.execute(
            f"select * from public.agent_slots where agent_server_id = %s{clause}"
            " order by slot_index",
            (host_id,),
        ).fetchall()


def next_slot_index(settings: Settings, host_id: str) -> int:
    with _connect(settings) as conn:
        row = conn.execute(
            "select coalesce(max(slot_index), 0) + 1 as n from public.agent_slots"
            " where agent_server_id = %s and status = 'active'",
            (host_id,),
        ).fetchone()
    return int(row["n"])


def raise_service_incident(
    settings: Settings,
    org_id: str,
    worker_id: str,
    message: str,
    kind: str = "agent-service",
) -> bool:
    """Once per transition, not once per probe (US-26.7). True if raised."""
    with _connect(settings) as conn:
        recent = conn.execute(
            "select 1 from public.runner_incidents"
            " where worker_id = %s and kind = %s"
            " and created_at > now() - interval '1 hour' limit 1",
            (worker_id, kind),
        ).fetchone()
        if recent:
            return False
        conn.execute(
            "insert into public.runner_incidents (org_id, worker_id, kind, message)"
            " values (%s, %s, %s, %s)",
            (org_id, worker_id, kind, message),
        )
        conn.commit()
    return True


def revoked_slot_workers(settings: Settings, host_id: str) -> list[dict[str, Any]]:
    """Live slots on this host whose worker token has been revoked (US-27.9).

    For an agent Build Mill installed, a revoked token means the machine is
    running and useless: the control socket stays up (its handshake already
    succeeded) and keeps heartbeating, while every HTTP pool poll is rejected.
    Nothing else about the machine looks wrong — which is how fourteen minutes
    disappeared on 2026-07-26 — so the probe is where it has to be caught."""
    with _connect(settings) as conn:
        return conn.execute(
            "select s.id, s.name, s.slot_index, s.org_id, s.worker_id"
            " from public.agent_slots s"
            " join public.workers w on w.id = s.worker_id"
            " where s.agent_server_id = %s and s.status = 'active'"
            "   and w.status <> 'active'",
            (host_id,),
        ).fetchall()


def reap_orphaned_jobs(settings: Settings) -> int:
    """A restart killed the in-process task; the row must not stay running."""
    with _connect(settings) as conn:
        rows = conn.execute(
            "update public.agent_server_jobs"
            " set status = 'failed', finished_at = now(),"
            "     error = coalesce(error, 'interrupted by an API server restart'),"
            "     log = log || E'\\n[interrupted by an API server restart]'"
            " where status in ('queued', 'running') returning id"
        ).fetchall()
        conn.commit()
        return len(rows)


# ---------------------------------------------------------------------------
# Pool placement queue (US-57.3 follow-on)
#
# A shared pool's one-job-per-host lock means a placement request made while
# another job holds it cannot start yet — that used to be a hard 409 the
# tenant had to retry by hand. This table lets `place()` acknowledge the
# request immediately; `pool_placement_sweep` (run off the same liveness
# loop as `probe_sweep`) drains it once the host is free.
# ---------------------------------------------------------------------------


def upsert_pool_placement_request(
    settings: Settings,
    *,
    org_id: str,
    pool_id: str,
    worker_id: str,
    by: str = "",
    by_email: str = "",
) -> None:
    with _connect(settings) as conn:
        conn.execute(
            "insert into public.agent_pool_placement_requests"
            " (org_id, pool_id, worker_id, requested_by, requested_by_email)"
            " values (%s, %s, %s, %s, %s)"
            " on conflict (worker_id) do update set"
            "   pool_id = excluded.pool_id, status = 'pending', error = null,"
            "   requested_by = excluded.requested_by,"
            "   requested_by_email = excluded.requested_by_email,"
            "   created_at = now()",
            (org_id, pool_id, worker_id, by or None, by_email),
        )
        conn.commit()


def due_pool_placements(settings: Settings, limit: int = 5) -> list[dict[str, Any]]:
    with _connect(settings) as conn:
        return conn.execute(
            "select id, org_id, pool_id, worker_id, requested_by, requested_by_email"
            " from public.agent_pool_placement_requests"
            " where status = 'pending'"
            " order by created_at limit %s",
            (limit,),
        ).fetchall()


def _worker_already_placed(settings: Settings, worker_id: str) -> bool:
    with _connect(settings) as conn:
        row = conn.execute(
            "select 1 from public.agent_slots"
            " where worker_id = %s and status = 'active' limit 1",
            (worker_id,),
        ).fetchone()
    return row is not None


def _placement_pool_row(settings: Settings, pool_id: str) -> dict[str, Any] | None:
    with _connect(settings) as conn:
        return conn.execute(
            "select id, org_id, status, shared, pool_name, capacity"
            " from public.agent_servers where id = %s and shared limit 1",
            (pool_id,),
        ).fetchone()


def _placement_free_slots(settings: Settings, pool_id: str, capacity: int) -> int:
    with _connect(settings) as conn:
        row = conn.execute(
            "select count(*) as n from public.agent_slots"
            " where agent_server_id = %s and status = 'active'",
            (pool_id,),
        ).fetchone()
    used = row["n"] if row else 0
    return max((capacity or 0) - used, 0)


def fail_pool_placement(settings: Settings, request_id: str, error: str) -> None:
    with _connect(settings) as conn:
        conn.execute(
            "update public.agent_pool_placement_requests"
            " set status = 'failed', error = %s where id = %s",
            (error, request_id),
        )
        conn.commit()


def delete_pool_placement(settings: Settings, request_id: str) -> None:
    with _connect(settings) as conn:
        conn.execute(
            "delete from public.agent_pool_placement_requests where id = %s",
            (request_id,),
        )
        conn.commit()


async def pool_placement_sweep(settings: Settings) -> int:
    """Drain queued pool placements once their host's job lock frees up."""
    if not settings.database_url:
        return 0
    due = await asyncio.to_thread(due_pool_placements, settings)
    placed = 0
    for req in due:
        request_id = str(req["id"])
        worker_id = str(req["worker_id"])
        pool_id = str(req["pool_id"])

        if await asyncio.to_thread(_worker_already_placed, settings, worker_id):
            # Placed some other way (or a duplicate request) since queueing.
            await asyncio.to_thread(delete_pool_placement, settings, request_id)
            continue

        pool = await asyncio.to_thread(_placement_pool_row, settings, pool_id)
        if pool is None or pool["status"] != "ready":
            await asyncio.to_thread(
                fail_pool_placement, settings, request_id,
                "The pool is no longer available.",
            )
            continue

        free = await asyncio.to_thread(
            _placement_free_slots, settings, pool_id, pool["capacity"] or 0
        )
        if free < 1:
            await asyncio.to_thread(
                fail_pool_placement, settings, request_id,
                f"{pool['pool_name'] or 'The pool'} filled up before a slot "
                "freed for this agent.",
            )
            continue

        try:
            job = await asyncio.to_thread(
                create_job,
                settings,
                org_id=str(req["org_id"]),
                agent_server_id=pool_id,
                kind="add_slot",
                by=req["requested_by"] or "",
                by_email=req["requested_by_email"] or "",
            )
        except JobActive:
            continue  # still busy; the next sweep tries again

        await asyncio.to_thread(delete_pool_placement, settings, request_id)
        launch(
            settings,
            {
                "job_id": str(job["id"]),
                "agent_server_id": pool_id,
                "kind": "add_slot",
                "slots": 1,
                "adopt_worker_id": worker_id,
            },
        )
        placed += 1
    return placed


# ---------------------------------------------------------------------------
# Step context
# ---------------------------------------------------------------------------


class StepCtx:
    def __init__(
        self,
        *,
        transport: paramiko.Transport,
        host: dict[str, Any],
        password: str | None,
        log: Callable[[str], None],
        mask: Callable[[str], str],
    ):
        self.transport = transport
        self.host = host
        self.password = password
        self.log = log
        self.mask = mask
        self.versions: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


async def _resolve_password(settings: Settings, host: dict[str, Any]) -> str | None:
    """The sudo password, when the server authenticates by password."""
    if host["auth_method"] != "password":
        return None
    creds = await deploy._resolve_credentials(
        settings,
        {"org_id": str(host["org_id"]), "id": str(host["server_id"]), "auth_method": "password"},
    )
    return creds.password


async def run_job(settings: Settings, ctx: dict[str, Any]) -> None:
    """Run one job to completion, streaming its log."""
    job_id = ctx["job_id"]
    host_id = ctx["agent_server_id"]
    kind = ctx["kind"]

    host = await asyncio.to_thread(get_host, settings, host_id)
    if host is None:
        await asyncio.to_thread(
            update_job, settings, job_id,
            {"status": "failed", "error": "the agent server is gone", "finished_at": "now()"},
        )
        return

    state: dict[str, Any] = {"log": [], "unflushed": 0}

    def logline(line: str) -> None:
        state["log"].append(line)
        state["unflushed"] += 1

    def flush() -> None:
        state["unflushed"] = 0
        update_job(settings, job_id, {"log": "\n".join(state["log"])})

    conn: ssh.Connection | None = None
    outcome = "succeeded"
    error: str | None = None
    step_name: str | None = None
    try:
        # US-57.8 fix: this used to run BEFORE this try/finally — a missing
        # credential (exactly what a re-homed server has until its password
        # is re-entered) raised uncaught, so the job never reached the
        # `finally` below and sat at "running" forever with an empty log,
        # permanently blocking every later job on that host (one-at-a-time).
        password = await _resolve_password(settings, host)
        mask = make_masker([password])
        logline(f"[{kind} on {host['server_name']} ({host['host']})]")
        conn = await deploy.connect_to_server(
            settings,
            {
                "org_id": str(host["org_id"]),
                "id": str(host["server_id"]),
                "host": host["host"],
                "port": host["port"],
                "username": host["username"],
                "auth_method": host["auth_method"],
                "host_key_fingerprint": host["host_key_fingerprint"],
            },
        )
        step = StepCtx(
            transport=conn.transport, host=host, password=password, log=logline, mask=mask
        )
        handler = {
            "provision": _job_provision,
            "add_slot": _job_add_slot,
            "update": _job_update,
            "restart": _job_restart,
            "reissue_token": _job_reissue_token,
            "remove_slot": _job_remove_slot,
            "teardown": _job_teardown,
            "probe": _job_probe,
        }[kind]
        outcome = await handler(settings, step, ctx, flush) or "succeeded"
    except JobError as e:
        outcome, error, step_name = "failed", e.message, e.step
        logline(f"[FAILED at {e.step}: {e.message}]")
    except deploy.PipelineError as e:
        outcome, error = "failed", e.message
        logline(f"[FAILED: {e.message}]")
    except Exception as e:  # noqa: BLE001 — a job must always land somewhere
        outcome, error = "failed", str(e)
        logline(f"[FAILED: {e}]")
        logger.exception("agent server job %s failed", job_id)
    finally:
        if conn:
            conn.close()
        await asyncio.to_thread(
            update_job,
            settings,
            job_id,
            {
                "status": outcome,
                "error": error,
                "step": step_name,
                "log": "\n".join(state["log"]),
                "finished_at": _now(),
            },
        )
        # A probe never sets the host status from its outcome: a transient
        # probe failure must not knock a working host into `error`. US-57.9
        # adds the other half — a probe that succeeds promotes a recovered
        # host back to `ready` from inside `_probe_into_row`, so this
        # exclusion no longer makes `error` a state nothing can leave.
        if kind != "probe":
            await asyncio.to_thread(
                update_host,
                settings,
                host_id,
                {"status": _host_status_after(kind, outcome)},
            )


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())


def _host_status_after(kind: str, outcome: str) -> str:
    if kind == "teardown":
        return "removed" if outcome == "succeeded" else "error"
    if outcome == "failed":
        return "error"
    if outcome == "partial":
        return "degraded"
    return "ready"


async def _job_provision(
    settings: Settings, step: StepCtx, ctx: dict[str, Any], flush
) -> str:
    await asyncio.to_thread(update_host, settings, str(step.host["id"]), {"status": "provisioning"})

    step.log("== preflight ==")
    checks = await asyncio.to_thread(preflight, step.transport, step.host["workdir"], step.password)
    for check in checks:
        step.log(f"[{'ok' if check['ok'] else 'FAILED'}] {check['check']}: {check['detail']}")
    await asyncio.to_thread(flush)
    failed = [c for c in checks if not c["ok"]]
    if failed:
        raise JobError("preflight", "; ".join(f"{c['check']}: {c['detail']}" for c in failed))

    for name, fn in (
        ("base packages", install_base),
        ("service user", install_service_user),
        ("agent CLIs", install_modules),
        ("extra packages", install_extras),
    ):
        step.log(f"== {name} ==")
        await asyncio.to_thread(fn, step)
        await asyncio.to_thread(flush)

    step.log("== supervisor bundle ==")
    digest = await asyncio.to_thread(push_bundle, step)
    step.log("== systemd unit ==")
    await asyncio.to_thread(install_units, step)
    await asyncio.to_thread(flush)

    await asyncio.to_thread(
        update_host,
        settings,
        str(step.host["id"]),
        {
            "bundle_hash": digest,
            "agent_version": digest,
            "cli_versions": json.dumps(step.versions),
            "provisioned_at": _now(),
        },
    )

    wanted = int(ctx.get("slots") or 0)
    for _ in range(wanted):
        await _create_and_start_slot(settings, step, flush)

    await _probe_into_row(settings, step)
    step.log("[provision complete]")
    return "succeeded"


async def _create_and_start_slot(
    settings: Settings,
    step: StepCtx,
    flush,
    *,
    adopt_worker_id: str | None = None,
) -> dict[str, Any]:
    host_id = str(step.host["id"])
    org_id = str(step.host["org_id"])
    index = await asyncio.to_thread(next_slot_index, settings, host_id)
    base_name = re.sub(r"[^a-z0-9-]+", "-", (step.host["server_name"] or "agent").lower()).strip("-")
    name = f"{base_name}-{index}"
    workdir = step.host["workdir"]

    step.log(f"== slot {index} ==")
    if adopt_worker_id:
        token = await asyncio.to_thread(adopt_worker_token, settings, adopt_worker_id)
        identity = {"worker_id": adopt_worker_id, "principal_id": None, "token": token}
        with _connect(settings) as conn:
            row = conn.execute(
                "select name, principal_id, org_id from public.workers where id = %s",
                (adopt_worker_id,),
            ).fetchone()
        if row:
            name = row["name"]
            identity["principal_id"] = str(row["principal_id"]) if row["principal_id"] else None
            # US-57.3: the slot belongs to whoever the agent belongs to. On
            # every host this ran on before, that was already the host's own
            # org (adoption required it); on a shared pool it is the
            # tenant placing the agent, which the host's org_id cannot say.
            org_id = str(row["org_id"])
        step.log(f"[bound existing agent {name} — its token was re-issued for this machine]")
    else:
        identity = await asyncio.to_thread(
            create_slot_identity,
            settings,
            org_id=org_id,
            name=name,
            template=step.host.get("slot_template") or {},
        )
        step.log(f"[created agent {name}]")

    with _connect(settings) as conn:
        slot = conn.execute(
            "insert into public.agent_slots"
            " (org_id, agent_server_id, slot_index, name, worker_id, principal_id,"
            "  service_name, workspace_path, desired_state)"
            " values (%s, %s, %s, %s, %s, %s, %s, %s, 'paused') returning *",
            (
                org_id,
                host_id,
                index,
                name,
                identity["worker_id"],
                identity["principal_id"],
                f"buildmill-agent@{index}",
                f"{workdir}/agents/{index}/workspace",
                ),
        ).fetchone()
        conn.commit()

    slot_user: str | None = None
    if step.host.get("shared"):
        slot_user = await asyncio.to_thread(ensure_slot_user, step, index)
        await asyncio.to_thread(write_slot_service_override, step, index, slot_user)

    await asyncio.to_thread(
        write_slot_env, step, index, identity["token"], settings.api_base_url, user=slot_user
    )
    await asyncio.to_thread(
        run_ok,
        step.transport,
        f"systemctl enable --now buildmill-agent@{index}",
        step=f"slot {index} start",
        sudo=True,
        password=step.password,
        log=step.log,
        mask=step.mask,
    )
    await asyncio.to_thread(flush)

    connected = await _wait_for_connect(settings, str(identity["worker_id"]), step.log)
    if not connected:
        step.log(connect_timeout_message(index, settings.api_base_url))
    else:
        step.log(f"[slot {index} connected — paused, claiming nothing until you enable it]")
    return slot


async def _wait_for_connect(
    settings: Settings, worker_id: str, log: Callable[[str], None]
) -> bool:
    deadline = time.monotonic() + CONNECT_WAIT_SECONDS

    def connected() -> bool:
        # us-116.4: the one presence predicate (a heartbeat inside the window).
        with _connect(settings) as conn:
            row = conn.execute(
                "select 1 from public.live_runner_sessions"
                " where worker_id = %s limit 1",
                (worker_id,),
            ).fetchone()
        return row is not None

    while time.monotonic() < deadline:
        if await asyncio.to_thread(connected):
            return True
        await asyncio.sleep(3)
    return False


async def _job_add_slot(settings: Settings, step: StepCtx, ctx: dict[str, Any], flush) -> str:
    for _ in range(int(ctx.get("slots") or 1)):
        await _create_and_start_slot(
            settings, step, flush, adopt_worker_id=ctx.get("adopt_worker_id")
        )
    await _probe_into_row(settings, step)
    return "succeeded"


async def _job_update(settings: Settings, step: StepCtx, ctx: dict[str, Any], flush) -> str:
    step.log("== toolchain ==")
    for name, fn in (
        ("base packages", install_base),
        ("service user", install_service_user),
        ("agent CLIs", install_modules),
        ("extra packages", install_extras),
    ):
        step.log(f"== {name} ==")
        await asyncio.to_thread(fn, step)
        await asyncio.to_thread(flush)

    step.log("== supervisor bundle ==")
    digest = await asyncio.to_thread(push_bundle, step)
    await asyncio.to_thread(install_units, step)
    await asyncio.to_thread(
        update_host,
        settings,
        str(step.host["id"]),
        {
            "bundle_hash": digest,
            "agent_version": digest,
            "cli_versions": json.dumps(step.versions),
        },
    )

    slots = await asyncio.to_thread(get_slots, settings, str(step.host["id"]))
    skipped: list[str] = []
    for slot in slots:
        previous = slot["desired_state"]
        if previous == "stopped":
            step.log(f"[slot {slot['slot_index']} is stopped — leaving it alone]")
            continue
        step.log(f"== restarting slot {slot['slot_index']} ==")
        # US-26.8: converge the slot's env file too, not just the code. A host
        # whose API URL changed after it was provisioned would otherwise have
        # agents dialling the old address forever, with removing and re-adding
        # every slot as the only repair.
        current_url = await asyncio.to_thread(
            read_slot_api_url, step, int(slot["slot_index"])
        )
        # US-27.13: NEVER re-point a slot at an address no remote machine can
        # reach. This fired for real on 2026-07-26: the deployed API had no
        # API_BASE_URL, so `settings.api_base_url` was the default
        # http://localhost:8000, and this convergence "corrected" two working
        # agents from https://api.buildmill.dev to their own loopback — taking
        # the whole fleet down and re-issuing both tokens on the way. A
        # convergence that makes a machine worse is not convergence.
        url_problem = api_url_problem(settings.api_base_url)
        if url_problem and current_url:
            step.log(
                f"[slot {slot['slot_index']} keeps {current_url} — NOT "
                f"re-pointed, because {url_problem}]"
            )
        elif url_problem:
            raise JobError("slot env", url_problem)
        elif current_url != settings.api_base_url:
            step.log(
                f"[slot {slot['slot_index']} points at "
                f"{current_url or 'nothing readable'} — re-pointing it at "
                f"{settings.api_base_url}; its token is re-issued because the old "
                "one cannot be read back]"
            )
            token = await asyncio.to_thread(
                reissue_worker_token, settings, str(slot["worker_id"])
            )
            await asyncio.to_thread(
                write_slot_env,
                step,
                int(slot["slot_index"]),
                token,
                settings.api_base_url,
                # Without the per-slot owner, this chown -R hands a shared
                # host's slot directory to SERVICE_USER and the slot's own
                # service can no longer write its workspace (2026-08-09:
                # every clone answered Permission denied until an Update).
                user=slot_unix_user(int(slot["slot_index"]))
                if step.host.get("shared")
                else None,
            )
        idle = await drain(settings, str(slot["worker_id"]), step.log)
        if not idle:
            skipped.append(str(slot["slot_index"]))
            await asyncio.to_thread(set_paused, settings, str(slot["worker_id"]), previous == "paused")
            await push_config(settings, str(slot["worker_id"]))
            continue
        # Self-heal any workspace-ownership drift on every routine update —
        # unlike write_slot_env's token/URL write above, this is always safe
        # to re-run and was previously only reachable by re-issuing the
        # slot's token.
        slot_owner = slot_unix_user(int(slot["slot_index"])) if step.host.get("shared") else None
        await asyncio.to_thread(
            ensure_slot_workspace, step, int(slot["slot_index"]), slot_owner
        )
        await asyncio.to_thread(
            run_ok,
            step.transport,
            f"systemctl restart buildmill-agent@{slot['slot_index']}",
            step=f"slot {slot['slot_index']} restart",
            sudo=True,
            password=step.password,
            log=step.log,
            mask=step.mask,
        )
        await asyncio.to_thread(
            update_slot, settings, str(slot["id"]), {"agent_version": digest}
        )
        # restore what the slot was before the drain paused it
        await asyncio.to_thread(
            set_paused, settings, str(slot["worker_id"]), previous == "paused"
        )
        await push_config(settings, str(slot["worker_id"]))
        step.log(f"[slot {slot['slot_index']} back on {digest}, {previous}]")
        await asyncio.to_thread(flush)

    await _probe_into_row(settings, step)
    if skipped:
        step.log(
            f"[{len(slots) - len(skipped)} of {len(slots)} agents updated — "
            f"slot(s) {', '.join(skipped)} were still running work and kept their old code]"
        )
        return "partial"
    return "succeeded"


async def _job_restart(settings: Settings, step: StepCtx, ctx: dict[str, Any], flush) -> str:
    slot = await asyncio.to_thread(_get_slot, settings, ctx["slot_id"])
    if slot is None:
        raise JobError("restart", "that agent is gone")
    previous = slot["desired_state"]
    idle = await drain(settings, str(slot["worker_id"]), step.log)
    if not idle:
        await asyncio.to_thread(set_paused, settings, str(slot["worker_id"]), previous == "paused")
        raise JobError("restart", "the agent is still running work — try again when it hands back")
    slot_owner = slot_unix_user(int(slot["slot_index"])) if step.host.get("shared") else None
    await asyncio.to_thread(ensure_slot_workspace, step, int(slot["slot_index"]), slot_owner)
    await asyncio.to_thread(
        run_ok,
        step.transport,
        f"systemctl restart buildmill-agent@{slot['slot_index']}",
        step="restart",
        sudo=True,
        password=step.password,
        log=step.log,
        mask=step.mask,
    )
    await asyncio.to_thread(set_paused, settings, str(slot["worker_id"]), previous == "paused")
    await push_config(settings, str(slot["worker_id"]))
    step.log(f"[slot {slot['slot_index']} restarted, {previous}]")
    return "succeeded"


async def _job_reissue_token(
    settings: Settings, step: StepCtx, ctx: dict[str, Any], flush
) -> str:
    """US-27.9: mint a new token, write it to the machine, restart, confirm.

    The ordering is the whole job. Mint first (the row must be valid before
    anything on the machine references it), write the env file over the SFTP
    channel `api` already holds, then restart — a service restarted before the
    file is written comes back with the dead token and the manager watches it
    fail twice."""
    slot = await asyncio.to_thread(_get_slot, settings, ctx["slot_id"])
    if slot is None:
        raise JobError("re-issue", "that agent is gone")
    if not slot.get("worker_id"):
        raise JobError("re-issue", "this slot has no agent identity to re-issue")
    index = int(slot["slot_index"])

    problem = api_url_problem(settings.api_base_url)
    if problem:
        raise JobError("re-issue", problem)

    step.log(f"== re-issuing slot {index}'s token ==")
    token = await asyncio.to_thread(
        reissue_worker_token, settings, str(slot["worker_id"])
    )
    step.log("[minted a new worker token — the previous one is dead]")
    # The per-slot owner matters as much as the token: write_slot_env chowns
    # the whole slot directory, and defaulting to SERVICE_USER on a shared
    # host hands the workspace to the wrong user — the slot's service then
    # fails every clone with Permission denied (2026-08-09, slots 5/6 on
    # Pod-001, via both the manual Re-issue and auto-repair's second rung).
    slot_owner = slot_unix_user(index) if step.host.get("shared") else None
    await asyncio.to_thread(
        write_slot_env, step, index, token, settings.api_base_url, user=slot_owner
    )
    await asyncio.to_thread(
        run_ok,
        step.transport,
        f"systemctl restart buildmill-agent@{index}",
        step="re-issue",
        sudo=True,
        password=step.password,
        log=step.log,
        mask=step.mask,
    )
    await asyncio.to_thread(flush)
    if await _wait_for_connect(settings, str(slot["worker_id"]), step.log):
        step.log(f"[slot {index} reconnected with its new token]")
        return "succeeded"
    step.log(connect_timeout_message(index, settings.api_base_url))
    return "partial"


def _get_slot(settings: Settings, slot_id: str) -> dict[str, Any] | None:
    with _connect(settings) as conn:
        return conn.execute(
            "select * from public.agent_slots where id = %s", (slot_id,)
        ).fetchone()


async def _remove_one_slot(
    settings: Settings,
    step: StepCtx,
    slot: dict[str, Any],
    force: bool,
    purge: bool = False,
) -> None:
    index = slot["slot_index"]
    step.log(f"== removing slot {index} ({slot['name']}) ==")
    if slot.get("worker_id"):
        idle = await drain(settings, str(slot["worker_id"]), step.log)
        if not idle and not force:
            raise JobError(
                f"slot {index}",
                "the agent is still running work — wait for it to hand back, or force removal",
            )
    workdir = step.host["workdir"]
    await asyncio.to_thread(
        run_ok,
        step.transport,
        f"systemctl disable --now buildmill-agent@{index} 2>/dev/null || true;"
        f" rm -f {_q(workdir)}/env/{index}.env",
        step=f"slot {index} stop",
        sudo=True,
        password=step.password,
        log=step.log,
        mask=step.mask,
    )
    if purge:
        # US-57.18: the agent was deleted org-side, so its files are garbage —
        # unlike a manual Remove, where the workspace can hold the only copy
        # of a diff and is deliberately kept.
        await asyncio.to_thread(
            run_ok,
            step.transport,
            f"rm -rf {_q(workdir)}/agents/{index}",
            step=f"slot {index} purge",
            sudo=True,
            password=step.password,
            log=step.log,
            mask=step.mask,
        )
    await asyncio.to_thread(retire_slot_identity, settings, slot)
    await asyncio.to_thread(
        update_slot,
        settings,
        str(slot["id"]),
        {"status": "removed", "desired_state": "stopped", "service_state": "inactive"},
    )
    if purge:
        step.log(f"[slot {index} removed — files purged, identity kept for history]")
    else:
        step.log(f"[slot {index} removed — token revoked, agent kept for history]")


async def _job_remove_slot(settings: Settings, step: StepCtx, ctx: dict[str, Any], flush) -> str:
    slot = await asyncio.to_thread(_get_slot, settings, ctx["slot_id"])
    if slot is None:
        raise JobError("remove", "that agent is gone")
    await _remove_one_slot(
        settings, step, slot, bool(ctx.get("force")), purge=bool(ctx.get("purge"))
    )
    return "succeeded"


async def _job_teardown(settings: Settings, step: StepCtx, ctx: dict[str, Any], flush) -> str:
    force = bool(ctx.get("force"))
    slots = await asyncio.to_thread(get_slots, settings, str(step.host["id"]))
    for slot in slots:
        await _remove_one_slot(settings, step, slot, force)
        await asyncio.to_thread(flush)

    workdir = step.host["workdir"]
    step.log("== removing the unit template ==")
    await asyncio.to_thread(
        run_ok,
        step.transport,
        f"rm -f /etc/systemd/system/{UNIT_TEMPLATE_NAME}"
        f" /etc/sudoers.d/buildmill-agent && systemctl daemon-reload",
        step="teardown",
        sudo=True,
        password=step.password,
        log=step.log,
        mask=step.mask,
    )
    if ctx.get("wipe_workdir"):
        step.log(f"== wiping {workdir} ==")
        await asyncio.to_thread(
            run_ok,
            step.transport,
            f"rm -rf {_q(workdir)}",
            step="wipe",
            sudo=True,
            password=step.password,
            log=step.log,
            mask=step.mask,
        )
    else:
        step.log(f"[{workdir} left in place — workspaces can hold the only copy of a diff]")
    step.log("[teardown complete — the server registration itself is untouched]")
    return "succeeded"


async def _job_probe(settings: Settings, step: StepCtx, ctx: dict[str, Any], flush) -> str:
    await _probe_into_row(settings, step)
    return "succeeded"


async def _probe_into_row(settings: Settings, step: StepCtx) -> None:
    host_id = str(step.host["id"])
    slots = await asyncio.to_thread(get_slots, settings, host_id)
    indexes = [int(s["slot_index"]) for s in slots]
    try:
        result = await asyncio.to_thread(
            probe_host, step.transport, step.host["workdir"], indexes
        )
    except JobError as e:
        await asyncio.to_thread(
            update_host, settings, host_id, {"probe_error": e.message, "last_probe_at": _now()}
        )
        return

    services = result.pop("services", {})
    installed = result.pop("bundle_hash", None)
    fields = {**result, "probe_error": None, "last_probe_at": _now()}
    if installed:
        fields["bundle_hash"] = installed
        fields["agent_version"] = installed
    await asyncio.to_thread(update_host, settings, host_id, fields)

    # US-57.9: `run_job` deliberately skips the status update for a probe, so a
    # flaky probe can never knock a working host into `error`. That also made
    # `error` a one-way door — nothing on a schedule could leave it, and a
    # healthy pool with 31 free slots stayed invisible to every tenant. A
    # probe that SUCCEEDS may promote a recovered host; it still never demotes.
    if await asyncio.to_thread(clear_host_error, settings, host_id):
        step.log("[probe succeeded — host status cleared back to ready]")

    for slot in slots:
        state = services.get(int(slot["slot_index"]), "unknown")
        slot_fields: dict[str, Any] = {"service_state": state, "last_service_check": _now()}
        # US-68.3: a slot the probe finds running again has recovered from
        # whatever the auto-repair ladder was climbing for it — start the
        # ladder over next time, rather than carrying attempts from an
        # unrelated, already-resolved episode into a future one.
        if state == "active" and (
            slot.get("auto_repair_attempts") or slot.get("auto_repair_needs_attention")
        ):
            slot_fields["auto_repair_attempts"] = 0
            slot_fields["auto_repair_needs_attention"] = False
        await asyncio.to_thread(
            update_slot, settings, str(slot["id"]), slot_fields
        )
        if slot["desired_state"] == "enabled" and state in {"failed", "inactive"}:
            step.log(
                f"[slot {slot['slot_index']} should be running but its service is {state}]"
            )
            if slot.get("worker_id"):
                await asyncio.to_thread(
                    raise_service_incident,
                    settings,
                    str(slot["org_id"]),
                    str(slot["worker_id"]),
                    f"{slot['name']}: systemd unit is {state} while the agent is enabled",
                )

    # US-27.9: a revoked token is an alarm, not a state. The service is
    # running, the socket is connected, and the agent cannot take a single
    # item of work.
    for revoked in await asyncio.to_thread(revoked_slot_workers, settings, host_id):
        message = (
            f"{revoked['name']}: this agent's worker token has been revoked. "
            "Its service is running and its socket may be connected, but every "
            "attempt to claim work is rejected. Re-issue the token and push it "
            "to the machine from the host's Agents tab."
        )
        step.log(f"[slot {revoked['slot_index']}: worker token revoked]")
        raised = await asyncio.to_thread(
            raise_service_incident,
            settings,
            str(revoked["org_id"]),
            str(revoked["worker_id"]),
            message,
            "agent-token",
        )
        if raised:
            await asyncio.to_thread(
                db.notify_org_managers,
                settings,
                str(revoked["org_id"]),
                "runner_fault",
                {
                    "worker": revoked["name"],
                    "run_id": None,
                    "message": message[:200],
                },
            )


# ---------------------------------------------------------------------------
# Launch + the background probe sweep
# ---------------------------------------------------------------------------


def launch(settings: Settings, ctx: dict[str, Any]) -> None:
    """Fire-and-forget a job on the running event loop."""
    task = asyncio.get_running_loop().create_task(run_job(settings, ctx))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


def hosts_due_for_probe(settings: Settings, older_than_seconds: int = 300) -> list[dict[str, Any]]:
    with _connect(settings) as conn:
        return conn.execute(
            "select a.id, a.org_id from public.agent_servers a"
            " where a.status in ('ready', 'degraded', 'error')"
            "   and (a.last_probe_at is null"
            "        or a.last_probe_at < now() - make_interval(secs => %s))"
            "   and not exists (select 1 from public.agent_server_jobs j"
            "                   where j.agent_server_id = a.id"
            "                     and j.status in ('queued', 'running'))"
            " order by a.last_probe_at nulls first limit 3",
            (older_than_seconds,),
        ).fetchall()


async def probe_sweep(settings: Settings) -> int:
    """Staggered health probes, run off the existing liveness loop (US-26.7)."""
    if not settings.database_url:
        return 0
    due = await asyncio.to_thread(hosts_due_for_probe, settings)
    for host in due:
        try:
            job = await asyncio.to_thread(
                create_job,
                settings,
                org_id=str(host["org_id"]),
                agent_server_id=str(host["id"]),
                kind="probe",
                by_email="system",
            )
        except JobActive:
            continue
        launch(
            settings,
            {"job_id": str(job["id"]), "agent_server_id": str(host["id"]), "kind": "probe"},
        )
    return len(due)


# ---------------------------------------------------------------------------
# Auto repair (US-68.3): a per-machine service, on by default, that notices a
# slot the probe found not actually running and works up an escalating
# ladder of fixes on its own — restart (which also self-heals workspace
# ownership, us-68.2), then re-issue its token, then a full Update as the
# last resort — instead of a human having to notice and click the button.
# ---------------------------------------------------------------------------

# Cheapest first. Each rung only runs if the previous one, tried on an
# earlier sweep, did not clear the problem — never more than one rung per
# sweep, so a genuinely broken machine escalates instead of being hammered.
AUTO_REPAIR_LADDER: tuple[str, ...] = ("restart", "reissue_token", "update")

# The sweep loop ticks roughly every 60s (US-26.7's liveness loop); this is
# the minimum gap between two auto-repair actions on the SAME slot, so the
# three rungs play out over up to 45 minutes rather than in one burst.
AUTO_REPAIR_COOLDOWN_SECONDS = 15 * 60


def next_auto_repair_action(attempts: int) -> str | None:
    """The next rung to try given how many have already been attempted for
    this slot's current degraded episode, or None once the ladder is
    exhausted — the caller flags the slot for a human instead of retrying."""
    if attempts < 0 or attempts >= len(AUTO_REPAIR_LADDER):
        return None
    return AUTO_REPAIR_LADDER[attempts]


def slots_due_for_repair(
    settings: Settings, older_than_seconds: int = AUTO_REPAIR_COOLDOWN_SECONDS
) -> list[dict[str, Any]]:
    with _connect(settings) as conn:
        return conn.execute(
            """
            select s.id, s.org_id, s.agent_server_id, s.slot_index, s.name,
                   s.auto_repair_attempts
            from public.agent_slots s
            join public.agent_servers a on a.id = s.agent_server_id
            where a.auto_repair_enabled
              and a.status not in ('new', 'provisioning', 'removed')
              and s.status = 'active'
              and s.desired_state = 'enabled'
              and s.service_state in ('failed', 'inactive')
              and not s.auto_repair_needs_attention
              and (s.auto_repair_last_at is null
                   or s.auto_repair_last_at < now() - make_interval(secs => %s))
              and not exists (
                select 1 from public.agent_server_jobs j
                where j.agent_server_id = s.agent_server_id
                  and j.status in ('queued', 'running')
              )
            order by s.auto_repair_last_at nulls first
            limit 3
            """,
            (older_than_seconds,),
        ).fetchall()


async def auto_repair_sweep(settings: Settings) -> int:
    """Run off the same liveness loop as probe_sweep — a slot only shows up
    here once a probe has already recorded it as failed/inactive."""
    if not settings.database_url:
        return 0
    due = await asyncio.to_thread(slots_due_for_repair, settings)
    for slot in due:
        action = next_auto_repair_action(int(slot["auto_repair_attempts"]))
        if action is None:
            await asyncio.to_thread(
                update_slot,
                settings,
                str(slot["id"]),
                {"auto_repair_needs_attention": True},
            )
            continue
        try:
            job = await asyncio.to_thread(
                create_job,
                settings,
                org_id=str(slot["org_id"]),
                agent_server_id=str(slot["agent_server_id"]),
                kind=action,
                slot_id=str(slot["id"]) if action != "update" else None,
                by_email="auto-repair",
            )
        except JobActive:
            # Something else has this host's one-job-per-host lock (a manual
            # action, or another slot's own repair job) — try again next tick.
            continue
        ctx = {
            "job_id": str(job["id"]),
            "agent_server_id": str(slot["agent_server_id"]),
            "kind": action,
        }
        if action != "update":
            ctx["slot_id"] = str(slot["id"])
        launch(settings, ctx)
        await asyncio.to_thread(
            update_slot,
            settings,
            str(slot["id"]),
            {
                "auto_repair_attempts": int(slot["auto_repair_attempts"]) + 1,
                "auto_repair_last_at": _now(),
            },
        )
    return len(due)


# ---------------------------------------------------------------------------
# US-57.18: a deleted agent leaves its machine. Org-side removal is a
# browser-side membership DELETE under RLS — no API endpoint sees it happen,
# so the machine footprint (running service, occupied slot, workspace on
# disk) survived it and the superadmin's machine page kept listing agents
# that no longer existed anywhere an org user could see. This sweep notices
# the orphans and runs the ordinary removal job on them, with the files
# purged — deletion is the explicit "this agent is gone" signal that makes
# the workspace garbage (a *manual* Remove still keeps it).
# ---------------------------------------------------------------------------

# The orphan signature is all three, not any one: the slot is live, its
# worker's token is revoked, and the worker's principal has NO membership row
# in the slot's org. A suspended member keeps its row (us-55.2 restores that
# agent); a deliberate token revoke keeps its row; only org-side deletion
# produces all three. Shared with the live-SQL test so the test exercises
# the exact predicate the sweep runs.
ORPHANED_SLOTS_SQL = """
    select s.id, s.org_id, s.agent_server_id, s.slot_index, s.name
    from public.agent_slots s
    join public.agent_servers a on a.id = s.agent_server_id
    join public.workers w on w.id = s.worker_id
    where s.status = 'active'
      and w.status = 'revoked'
      and w.principal_id is not null
      and not exists (
        select 1 from public.organization_members m
        where m.principal_id = w.principal_id
          and m.org_id = s.org_id
      )
      and a.status not in ('new', 'provisioning', 'removed')
      and not exists (
        select 1 from public.agent_server_jobs j
        where j.agent_server_id = s.agent_server_id
          and j.status in ('queued', 'running')
      )
      and not exists (
        select 1 from public.agent_server_jobs j
        where j.slot_id = s.id
          and j.kind = 'remove_slot'
          and j.created_at > now() - interval '15 minutes'
      )
    order by s.updated_at
    limit 3
"""


def orphaned_slots(settings: Settings) -> list[dict[str, Any]]:
    with _connect(settings) as conn:
        return conn.execute(ORPHANED_SLOTS_SQL).fetchall()


async def orphan_cleanup_sweep(settings: Settings) -> int:
    """Rides the liveness loop like auto_repair_sweep. The 15-minute
    per-slot guard in the query bounds retries when a cleanup job fails —
    a later sweep tries again, not every tick."""
    if not settings.database_url:
        return 0
    due = await asyncio.to_thread(orphaned_slots, settings)
    for slot in due:
        try:
            job = await asyncio.to_thread(
                create_job,
                settings,
                org_id=str(slot["org_id"]),
                agent_server_id=str(slot["agent_server_id"]),
                kind="remove_slot",
                slot_id=str(slot["id"]),
                by_email="orphan-cleanup",
            )
        except JobActive:
            # Something else holds this host's one-job-per-host lock —
            # try again next tick.
            continue
        launch(
            settings,
            {
                "job_id": str(job["id"]),
                "agent_server_id": str(slot["agent_server_id"]),
                "kind": "remove_slot",
                "slot_id": str(slot["id"]),
                # The worker is revoked: nothing claimable is in flight worth
                # draining, and the files go with the agent.
                "force": True,
                "purge": True,
            },
        )
    return len(due)
