"""Shared git plumbing for CLI agent modules (US-10.5).

Lifted from the retired `provider_claude` and routed through the runner's
`run_shell` primitive so every git command is audited (US-10.7) and unit-testable
with a fake Primitives. Commit identity is passed via `-c` flags so it needs no
global git config.

US-89.1: no credential ever rides a URL. The factory remote stays CLEAN in
`.git/config`, and authentication happens through a per-repo git credential
helper that reads `FACTORY_WORKER_TOKEN` from the process environment at
fetch/push time. The token used to be embedded as HTTP Basic in the remote —
which put it in `.git/config` on disk and verbatim into every audit row that
recorded a `remote set-url`; both leaks are gone by construction, and rotation
needs no workspace touched (the next git call reads the new env).
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .modules.base import Primitives

OUT_DIR = ".factory-out"
_IDENT = [
    "-c", "user.name=Software Factory",
    "-c", "user.email=factory@localhost",
]

# US-89.1: the credential helper, verbatim. A shell function (git runs helpers
# through sh, on Windows too via git's own sh) that answers the credential
# protocol from the environment — the variable name appears in git config and
# audit rows, the value never does. Configured per-repo by prepare_checkout,
# so the CLI agent's own `git push` inside a run authenticates the same way.
CRED_HELPER = (
    '!f() { echo username=worker; echo "password=$FACTORY_WORKER_TOKEN"; }; f'
)


class GitError(RuntimeError):
    pass


def clean_url(remote: str) -> str:
    """The remote with any userinfo stripped — what `.git/config` may hold.

    Also the repair for workspaces created before US-89.1, whose remotes
    still carry `worker:<token>@`: prepare_checkout writes this over them.
    """
    parts = urlsplit(remote)
    if parts.scheme not in ("http", "https"):
        return remote
    host = parts.netloc.rsplit("@", 1)[-1]
    return urlunsplit((parts.scheme, host, *parts[2:]))


def workspace_root() -> Path:
    root = os.environ.get("RUNNER_WORKSPACE") or str(
        Path(__file__).resolve().parent / "workspace"
    )
    Path(root).mkdir(parents=True, exist_ok=True)
    return Path(root)


async def git(prim: Primitives, args: list[str], cwd: str | None = None, timeout: int = 300) -> str:
    res = await prim.run_shell(["git", *args], cwd=cwd, timeout=timeout)
    if res.exit_code != 0:
        raise GitError(f"git {args[0]} failed: {res.stdout.strip()[:400]}")
    return res.stdout


async def prepare_checkout(
    prim: Primitives, remote: str, issue: str, project_id: str | None = None
) -> Path:
    """Clone (or fetch, on retry) the factory remote into the workdir.

    US-31.8: the workdir is the PROJECT's, not the work item's — ten stories
    on one project shared ten clones and ten dependency installs, and nothing
    ever pruned them. `issue` remains the fallback for a run that carries no
    project (an issue-less kind, or an older server).

    US-89.1: `remote` is stored CLEAN; auth rides the credential helper. A
    pre-89.1 workspace whose remote still embeds a token is scrubbed here.
    """
    from . import workspace

    url = clean_url(remote)
    workdir = workspace.workspace_for(project_id, issue)
    if (workdir / ".git").exists():
        await git(prim, ["config", "credential.helper", CRED_HELPER], cwd=str(workdir))
        await git(prim, ["remote", "set-url", "origin", url], cwd=str(workdir))
        await git(prim, ["fetch", "origin"], cwd=str(workdir))
    else:
        if workdir.exists():
            import shutil

            shutil.rmtree(workdir, ignore_errors=True)
        # `clone --config` both authenticates the initial fetch and persists
        # the helper into the new repo's config in one move.
        await git(
            prim,
            [
                "clone",
                "--config", f"credential.helper={CRED_HELPER}",
                url,
                str(workdir),
            ],
        )
    return workdir


async def checkout_branch(
    prim: Primitives, workdir: Path, branch: str, default_branch: str
) -> None:
    """Continue the branch when it exists upstream (retry), else cut it from
    the default branch head."""
    heads = await git(prim, ["ls-remote", "--heads", "origin", branch], cwd=str(workdir))
    if heads.strip():
        await git(prim, ["checkout", "-B", branch, f"origin/{branch}"], cwd=str(workdir))
        return
    try:
        await git(prim, ["checkout", "-B", branch, f"origin/{default_branch}"], cwd=str(workdir))
    except GitError:
        await git(prim, ["checkout", "-B", branch], cwd=str(workdir))  # empty repo


async def unsubmitted_paths(
    prim: Primitives, workdir: Path, branch: str
) -> list[str]:
    """us-96.8 AC5: locally-modified paths whose changes never reached the
    remote branch — the files an MCP hand-back (submit_changeset) left
    behind. The factory's commit is on origin/<branch>; anything dirty here
    that its diff does not cover was modified and never submitted. Factory
    scratch is excluded — it is legitimately never submitted."""
    await git(prim, ["fetch", "origin", branch], cwd=str(workdir), timeout=300)
    landed_raw = await git(
        prim,
        ["diff", "--name-only", f"HEAD..origin/{branch}"],
        cwd=str(workdir),
    )
    landed = {p.strip() for p in landed_raw.splitlines() if p.strip()}
    dirty_raw = await git(prim, ["status", "--porcelain"], cwd=str(workdir))
    scratch_prefixes = (OUT_DIR + "/", ".factory-", ".grok/")
    out: list[str] = []
    for line in dirty_raw.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip().strip('"')
        if " -> " in path:
            path = path.split(" -> ")[-1].strip().strip('"')
        if not path or path in landed:
            continue
        if path.startswith(scratch_prefixes) or path == OUT_DIR:
            continue
        out.append(path)
    return sorted(out)


async def commit_all_and_push(
    prim: Primitives, workdir: Path, branch: str, message: str
) -> bool:
    """Stage everything, commit (if there's anything to commit), and push the
    branch. Returns True if a commit was made."""
    status = await git(prim, ["status", "--porcelain"], cwd=str(workdir))
    made = bool(status.strip())
    if made:
        await git(prim, ["add", "-A"], cwd=str(workdir))
        await git(prim, [*_IDENT, "commit", "-m", message], cwd=str(workdir))
    await git(prim, ["push", "origin", branch], cwd=str(workdir), timeout=600)
    return made
