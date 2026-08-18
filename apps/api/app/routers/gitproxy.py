"""Factory git remote — smart-HTTP proxy (US-3.8).

All worker git traffic flows through the factory: HTTP Basic auth with
the worker's registry token, upstream requests to GitHub carrying the
org's GitHub credential (App installation token or PAT), which never
leaves this process, and pushes policy-checked *before* a byte reaches
GitHub. Pure pass-through — the factory stores no repo data; GitHub
stays the source of truth.

Push policy: every ref update must be a branch-create or forward move
on refs/heads/factory/issue-<id> for a run the authenticated worker
currently holds. Deletions, other branches, and history rewrites
(old head ≠ the last recorded push) are refused with a readable git
`ERR` pkt-line. True non-fast-forward detection needs the object graph,
which a pass-through proxy doesn't have — the recorded-head check plus
GitHub's own receive-pack are the enforcement pair.
"""

import asyncio
import base64
import time
import uuid as _uuid_mod
import zlib
from typing import Any, AsyncIterator

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from .. import db, github, github_tokens
from ..config import Settings, get_settings

router = APIRouter(tags=["git"])

ZERO_SHA = "0" * 40
HEADS_PREFIX = "refs/heads/"
# US-7.3: title-slug branches (factory/<slug>-<id6>) and, for the `main`
# strategy, the default branch itself are now valid push targets. A run is
# matched by the branch_ref stored on it at context-serve time. The legacy
# factory/issue-<uuid> naming stays parseable for in-flight runs.
LEGACY_BRANCH_PREFIX = "refs/heads/factory/issue-"
_BASIC_CHALLENGE = {"WWW-Authenticate": 'Basic realm="software-factory"'}

# Tokens are resolved per org+repo and cached (~1 h GitHub expiry, matching
# the App installation token's own lifetime); they never appear in a
# response, error, or log. Because of this cache, a disconnected or
# rotated credential can keep serving proxy traffic for up to the 50-minute
# TTL below — revoking the token on GitHub itself is the hard cutoff.
_token_cache: dict[str, tuple[str, float]] = {}
_token_lock = asyncio.Lock()


class _ProtocolError(Exception):
    pass


def _pkt(data: bytes) -> bytes:
    return f"{len(data) + 4:04x}".encode() + data


def _git_err(message: str, sideband: bool = False) -> Response:
    """A policy refusal git clients print verbatim ('remote error: …').
    When the client negotiated side-band, the message must ride band 3
    (the error band); otherwise a plain ERR pkt-line is understood."""
    text = f"{message}\n".encode()
    if sideband:
        body = _pkt(b"\x03" + text) + b"0000"
    else:
        body = _pkt(b"ERR " + text) + b"0000"
    return Response(
        content=body,
        media_type="application/x-git-receive-pack-result",
    )


def _valid_uuid(value: str) -> bool:
    try:
        _uuid_mod.UUID(value)
        return True
    except (ValueError, TypeError):
        return False


def _auth_worker(request: Request, settings: Settings) -> dict[str, Any]:
    """HTTP Basic — password is the worker token; username is ignored.
    401 carries the Basic challenge so git clients retry with creds."""
    header = request.headers.get("authorization", "")
    if header.lower().startswith("basic "):
        try:
            raw = base64.b64decode(header.split(" ", 1)[1]).decode()
            _, _, password = raw.partition(":")
        except Exception:  # noqa: BLE001 — malformed header is just a 401
            password = ""
        worker = db.get_worker_by_token(settings, password)
        if worker:
            return worker
    raise HTTPException(
        status_code=401,
        detail="authentication required",
        headers=_BASIC_CHALLENGE,
    )


def _resolve_repo(
    org_shortname: str,
    project_spec: str,
    worker: dict[str, Any],
    settings: Settings,
    check_capabilities: bool = False,
) -> tuple[str, str]:
    """US-3.13: the remote is addressed /git/<org-shortname>/<slug>.git —
    resolved against the worker's own org, so a foreign shortname is a 404.

    US-3.12: clone/fetch passes check_capabilities — a project outside an
    allow-listed worker's list answers 404 exactly like a cross-org one.
    The push path never checks capabilities: a claim already held is
    worked to completion even if capabilities changed after the claim."""
    project_slug = project_spec.removesuffix(".git")
    repo = db.get_project_repo(
        settings, org_shortname, project_slug, str(worker["org_id"])
    )
    if not repo:
        raise HTTPException(status_code=404, detail="repository not found")
    if check_capabilities and not db.worker_allowed_for_project(
        settings, str(worker["id"]), str(repo["id"])
    ):
        raise HTTPException(status_code=404, detail="repository not found")
    return str(repo["id"]), repo["repo_full_name"]


def _cache_key(org_id: str, repo_full_name: str) -> str:
    return f"{org_id}:{repo_full_name.lower()}"


async def _evict_token(org_id: str, repo_full_name: str) -> None:
    """Drop a cached credential GitHub has just refused, so the next
    resolution re-reads the org's connection instead of replaying it."""
    async with _token_lock:
        _token_cache.pop(_cache_key(org_id, repo_full_name), None)


async def _repo_token(
    settings: Settings, org_id: str, repo_full_name: str, fresh: bool = False
) -> str:
    cache_key = _cache_key(org_id, repo_full_name)
    if not fresh:
        async with _token_lock:
            hit = _token_cache.get(cache_key)
            if hit and hit[1] > time.time():
                return hit[0]
    # US-5.24: a broken org credential answers with the credential message
    # (403), not a bare "repository not found" 404 — the worker's git
    # client at least surfaces the right status to chase.
    try:
        token = await github_tokens.token_for_org(settings, org_id, repo_full_name)
    except (github.GitHubNotConfigured, github.GitHubCredentialError) as e:
        raise HTTPException(status_code=403, detail=e.message)
    except github.GitHubError as e:
        raise HTTPException(status_code=404, detail=e.message)
    async with _token_lock:
        _token_cache[cache_key] = (token, time.time() + 50 * 60)
    return token


def _upstream_headers(token: str, request: Request) -> dict[str, str]:
    headers = {
        "Authorization": "Basic "
        + base64.b64encode(f"x-access-token:{token}".encode()).decode(),
        "User-Agent": request.headers.get("user-agent", "software-factory-git"),
    }
    for name in ("git-protocol", "content-type", "accept"):
        if request.headers.get(name):
            headers[name.title()] = request.headers[name]
    return headers


# us-117.1: the transport failures that mean "nobody answered", as opposed to
# an answer we did not like. Mirrors supabase._TRANSPORT_FAILURES — one list
# per upstream, both saying the same thing about the same httpx classes.
_TRANSPORT_FAILURES = (
    httpx.ConnectTimeout,
    httpx.ConnectError,
    httpx.ReadTimeout,
    httpx.PoolTimeout,
    httpx.ReadError,
    httpx.RemoteProtocolError,
)


async def _upstream_stream(
    method: str,
    url: str,
    headers: dict[str, str],
    content: AsyncIterator[bytes] | None = None,
) -> tuple[int, dict[str, str], AsyncIterator[bytes]]:
    """Streaming request to GitHub; both directions unbuffered.

    us-117.1: a transport failure here is answered in words. This file had no
    `except httpx.*` at all, so GitHub (or the network) not answering was an
    unhandled exception — a bare 500 on the git wire, which is what a worker's
    `git ls-remote` reported on 2026-08-17 with no hint of what had failed.
    Same shape as US-79.5's `SupabaseUnreachable`: 504, and name the upstream.
    """
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(300.0, connect=15.0), follow_redirects=True
    )
    req = client.build_request(method, url, headers=headers, content=content)
    try:
        resp = await client.send(req, stream=True)
    except _TRANSPORT_FAILURES as exc:
        await client.aclose()
        raise HTTPException(
            status_code=504,
            detail=(
                "GitHub did not answer this git request "
                f"({type(exc).__name__}) — try again."
            ),
        ) from exc

    async def body() -> AsyncIterator[bytes]:
        try:
            async for chunk in resp.aiter_raw():
                yield chunk
        finally:
            await resp.aclose()
            await client.aclose()

    return resp.status_code, dict(resp.headers), body()


async def _discard(body: AsyncIterator[bytes]) -> None:
    """Run an upstream body to exhaustion so its response and client close."""
    async for _ in body:
        pass


def _credential_refused() -> Response:
    """GitHub refused the org's credential even after a fresh resolve. Git
    prints a text/plain error body verbatim ('remote: …'), so this says who
    can fix it instead of relaying GitHub's 401 about username/password —
    advice that makes no sense for a factory remote."""
    return Response(
        content=(
            "the org's GitHub credential was rejected by GitHub — the manager "
            "must reconnect GitHub in Settings → GitHub\n"
        ).encode(),
        status_code=403,
        media_type="text/plain",
    )


async def _gunzip(stream: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    async for chunk in stream:
        out = d.decompress(chunk)
        if out:
            yield out
    tail = d.flush()
    if tail:
        yield tail


async def _next_chunk(it) -> bytes:
    try:
        return await it.__anext__()
    except StopAsyncIteration:
        raise _ProtocolError("unexpected end of push request")


async def _drain(it) -> None:
    """Discard the rest of a rejected push — answering before the client
    finishes writing reads as 'remote end hung up' instead of our ERR."""
    try:
        while True:
            await it.__anext__()
    except StopAsyncIteration:
        pass


async def _read_commands(it) -> tuple[list[bytes], bytes, bytes]:
    """Consume the receive-pack command section (pkt-lines up to the
    flush-pkt). Returns (command payloads, raw consumed bytes, leftover
    bytes already read past the flush) — the packfile is NOT read."""
    buf = b""
    consumed = bytearray()
    commands: list[bytes] = []
    while True:
        while len(buf) < 4:
            buf += await _next_chunk(it)
        try:
            length = int(buf[:4], 16)
        except ValueError:
            raise _ProtocolError("malformed pkt-line length")
        if length == 0:  # flush-pkt ends the command section
            consumed += buf[:4]
            return commands, bytes(consumed), buf[4:]
        if length < 4:
            raise _ProtocolError("malformed pkt-line length")
        while len(buf) < length:
            buf += await _next_chunk(it)
        commands.append(buf[4:length])
        consumed += buf[:length]
        buf = buf[length:]


@router.get("/git/{org_shortname}/{project_spec}/info/refs")
async def info_refs(
    org_shortname: str,
    project_spec: str,
    request: Request,
    service: str = "",
    settings: Settings = Depends(get_settings),
):
    worker = _auth_worker(request, settings)
    if service not in ("git-upload-pack", "git-receive-pack"):
        raise HTTPException(status_code=400, detail="smart HTTP only")
    _, repo_full = _resolve_repo(
        org_shortname,
        project_spec,
        worker,
        settings,
        # fetch handshakes are capability-gated; push handshakes are not —
        # the claim check at receive-pack governs those
        check_capabilities=service == "git-upload-pack",
    )
    org_id = str(worker["org_id"])
    url = f"{settings.git_upstream_base}/{repo_full}.git/info/refs?service={service}"
    token = await _repo_token(settings, org_id, repo_full)
    status, uheaders, body = await _upstream_stream(
        "GET", url, _upstream_headers(token, request)
    )
    if status == 401:
        # The cached credential outlives a reconnect by up to its TTL, so a
        # manager who has just fixed the connection would keep seeing GitHub's
        # 401 for the better part of an hour. A refusal buys exactly one retry
        # with a freshly resolved credential; a second 401 is real.
        await _discard(body)
        await _evict_token(org_id, repo_full)
        token = await _repo_token(settings, org_id, repo_full, fresh=True)
        status, uheaders, body = await _upstream_stream(
            "GET", url, _upstream_headers(token, request)
        )
        if status == 401:
            await _discard(body)
            await _evict_token(org_id, repo_full)
            return _credential_refused()
    return StreamingResponse(
        body,
        status_code=status,
        media_type=uheaders.get(
            "content-type", f"application/x-{service}-advertisement"
        ),
        headers={"Cache-Control": "no-cache"},
    )


@router.post("/git/{org_shortname}/{project_spec}/git-upload-pack")
async def upload_pack(
    org_shortname: str,
    project_spec: str,
    request: Request,
    settings: Settings = Depends(get_settings),
):
    """Clone / fetch — streaming pass-through, capability-gated (US-3.12)."""
    worker = _auth_worker(request, settings)
    _, repo_full = _resolve_repo(
        org_shortname, project_spec, worker, settings, check_capabilities=True
    )
    token = await _repo_token(settings, str(worker["org_id"]), repo_full)
    headers = _upstream_headers(token, request)
    if request.headers.get("content-encoding"):
        headers["Content-Encoding"] = request.headers["content-encoding"]
    status, uheaders, body = await _upstream_stream(
        "POST",
        f"{settings.git_upstream_base}/{repo_full}.git/git-upload-pack",
        headers,
        content=request.stream(),
    )
    if status == 401:
        # The request body is spent, so this one can't be retried — evicting
        # at least keeps the next handshake off the dead credential.
        await _evict_token(str(worker["org_id"]), repo_full)
    return StreamingResponse(
        body,
        status_code=status,
        media_type=uheaders.get(
            "content-type", "application/x-git-upload-pack-result"
        ),
        headers={"Cache-Control": "no-cache"},
    )


def _power_ref_check(
    settings: Settings,
    power: dict[str, Any],
    project_id: str,
    principal_id: str,
    old: str,
    new: str,
    ref: str,
    power_updates: list[tuple[str, str]],
) -> str | None:
    """US-9.19: police ONE ref update under a Power Git grant. Returns an error
    string to refuse the whole push, or None to permit it. The claimed-run
    requirement is gone; only the grant's four rails apply. Permitted branch
    fast-forwards are appended to power_updates so their head can be recorded
    (backing the force-push rail, since the proxy has no object graph)."""
    if not ref.startswith(HEADS_PREFIX):
        if not power["allow_tag_push"]:
            return f"Power Git: tag / non-branch refs are not permitted (got {ref})"
        return None  # tags stream through untracked
    branch_name = ref[len(HEADS_PREFIX):]
    if new == ZERO_SHA:
        if not power["allow_branch_delete"]:
            return "Power Git: branch deletion is not permitted"
        return None
    default_branch = power.get("default_branch")
    if (
        not power["allow_default_branch"]
        and default_branch
        and branch_name == default_branch
    ):
        return (
            f"Power Git: direct pushes to the default branch ({default_branch}) "
            "are not permitted"
        )
    if not power["allow_force_push"]:
        recorded = db.get_git_power_branch_head(
            settings, project_id, principal_id, branch_name
        )
        if recorded and old != recorded and old != ZERO_SHA:
            return "Power Git: history rewrite / force-push is not permitted"
    power_updates.append((branch_name, new))
    return None


@router.post("/git/{org_shortname}/{project_spec}/git-receive-pack")
async def receive_pack(
    org_shortname: str,
    project_spec: str,
    request: Request,
    settings: Settings = Depends(get_settings),
):
    """Push — the command section is validated before anything is
    forwarded; the packfile then streams through untouched."""
    worker = _auth_worker(request, settings)
    project_id, repo_full = _resolve_repo(org_shortname, project_spec, worker, settings)

    stream: AsyncIterator[bytes] = request.stream()
    if request.headers.get("content-encoding", "").lower() == "gzip":
        stream = _gunzip(stream)
    it = stream.__aiter__()

    try:
        commands, consumed, leftover = await _read_commands(it)
    except _ProtocolError as e:
        await _drain(it)
        return _git_err(f"push rejected: {e}")

    # side-band capability decides how a refusal must be framed
    caps = commands[0].split(b"\x00", 1)[1] if commands and b"\x00" in commands[0] else b""
    sideband = b"side-band" in caps

    # US-9.19: a Power Git grant for this principal on this project lifts the
    # claimed-work-item requirement — the push is policed only by the grant's
    # rails. No grant (the common case) → the claim-based policy is unchanged.
    principal_id = worker.get("principal_id")
    power = (
        db.get_git_power_grant(settings, project_id, str(principal_id))
        if principal_id
        else None
    )

    error: str | None = None
    updates: list[tuple[str, str]] = []  # (run_id, new head sha) — claim path
    power_updates: list[tuple[str, str]] = []  # (branch, new head) — power path
    if not commands:
        error = "no ref updates in request"
    for line in commands:
        head = line.split(b"\x00", 1)[0].decode(errors="replace").strip()
        parts = head.split(" ")
        if len(parts) != 3:
            error = "malformed ref update"
            break
        old, new, ref = parts
        if power is not None:
            error = _power_ref_check(
                settings, power, project_id, str(principal_id),
                old, new, ref, power_updates,
            )
            if error:
                break
            continue
        if not ref.startswith(HEADS_PREFIX):
            error = f"only branch refs are writable through the factory remote (got {ref})"
            break
        if new == ZERO_SHA:
            error = "branch deletion is not allowed"
            break
        branch_name = ref[len(HEADS_PREFIX):]
        # Primary: match the push to the run's stored branch_ref (US-7.3).
        run = db.get_running_run_for_branch_ref(
            settings, project_id, branch_name, str(worker["id"])
        )
        # Legacy fallback: in-flight runs still on factory/issue-<uuid>.
        if not run and ref.startswith(LEGACY_BRANCH_PREFIX):
            issue_id = ref[len(LEGACY_BRANCH_PREFIX):]
            if _valid_uuid(issue_id):
                run = db.get_claimed_run_for_branch(
                    settings, project_id, issue_id, str(worker["id"])
                )
        if not run:
            error = (
                "no running claim of yours matches this branch — claim the "
                "work item first and push the branch the work context names"
            )
            break
        recorded = run.get("pushed_head_sha")
        if recorded and old != recorded and old != ZERO_SHA:
            error = (
                "history rewrite — the old head does not match the last push "
                "recorded by the factory"
            )
            break
        updates.append((str(run["id"]), new))

    if error:
        await _drain(it)
        return _git_err(f"push rejected: {error}", sideband=sideband)

    token = await _repo_token(settings, str(worker["org_id"]), repo_full)
    headers = _upstream_headers(token, request)
    headers["Content-Type"] = "application/x-git-receive-pack-request"
    headers.pop("Content-Encoding", None)  # forwarded stream is identity

    async def forward() -> AsyncIterator[bytes]:
        yield consumed
        if leftover:
            yield leftover
        while True:
            try:
                yield await it.__anext__()
            except StopAsyncIteration:
                return

    status, uheaders, body_iter = await _upstream_stream(
        "POST",
        f"{settings.git_upstream_base}/{repo_full}.git/git-receive-pack",
        headers,
        content=forward(),
    )
    # receive-pack responses are tiny (report-status only) — safe to buffer
    # so the push log records only what GitHub actually accepted.
    body = b""
    async for chunk in body_iter:
        body += chunk

    if status == 401:
        # Same as the fetch path: the packfile is spent, so evict and let the
        # next handshake resolve a live credential.
        await _evict_token(str(worker["org_id"]), repo_full)

    if status == 200 and b"unpack ok" in body and b"ng " not in body:
        for run_id, sha in updates:
            db.record_branch_push(settings, run_id, sha, worker["name"])
        for branch_name, sha in power_updates:
            db.record_git_power_branch_head(
                settings, project_id, str(principal_id), branch_name, sha
            )

    return Response(
        content=body,
        status_code=status,
        media_type=uheaders.get(
            "content-type", "application/x-git-receive-pack-result"
        ),
        headers={"Cache-Control": "no-cache"},
    )
