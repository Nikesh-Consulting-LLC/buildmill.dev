"""Software Factory API (US-1.8).

Deliberately thin: JWT verification + orchestration endpoints only.
CRUD lives in Supabase under RLS (see ARCHITECTURE.md "build less API").
"""

import asyncio
import logging
import pathlib
import time
from contextlib import asynccontextmanager

import websockets.http11
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

# Browsers attach every cookie scoped to the API's hostname to a WebSocket
# upgrade (on a dev machine, `localhost` accumulates auth cookies from every
# project ever run there — a few Supabase sessions exceed 8 KB on their own).
# Past its per-line default, the websockets handshake parser drops the request
# without sending any response, so the SSH terminal hangs at "Connecting"
# until the browser's ~4-minute socket timeout. Raise the limit well clear of
# any realistic Cookie header; the module attribute is read per-handshake, so
# assigning here covers uvicorn's already-imported parser too.
websockets.http11.MAX_LINE_LENGTH = 65536

from . import agent_provision, db, deploy, factory_mcp, reconcile
from . import pool  # US-87.6: connection pool lifecycle
from . import suites as suites_pipeline
from . import app_issues as app_issues_module
from .config import get_settings
from .errors import (
    WebSocketErrorReporter,
    is_client_disconnect,
    report_http_exception,
    scope_method,
    should_report,
)
from .routers import (
    admin,
    agent_pools,
    agent_servers,
    agents,
    app_issues,
    auth,
    deployments,
    github,
    gitproxy,
    issues,
    llm,
    llm_gateway,
    mcp_catalog,
    members,
    notifications,
    presets,
    projects,
    releases,
    reviews,
    agent_sessions,
    run_console,
    runner_socket,
    servers,
    suite_runs,
    worker,
    workflow,
)
from .supabase import PostgrestError, SupabaseUnreachable

logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # US-1.32: runs orphaned by a crash/redeploy of this process must never
    # stay 'running' (they'd hold the single-flight lock forever).
    try:
        reaped = deploy.reap_orphaned_runs(get_settings())
        if reaped:
            logger.warning("Reaped %d orphaned deployment run(s)", reaped)
    except Exception as e:  # DB unreachable — don't block startup
        logger.warning("Orphaned-run reaper skipped: %s", e)
    # US-119.1: a release at `deploying` is re-read from its own run — the
    # reaper above now settles the release for the runs it reaps, and this
    # catches everything the reaper cannot see: a release the old code
    # stranded, a settle that a DB hiccup swallowed, a run another process
    # left behind. It also runs on the liveness loop below.
    try:
        for r in deploy.settle_stranded_release_deploys(get_settings()):
            logger.warning(
                "Settled stranded release %s: deploy run %s was %s → %s",
                r["version"],
                r["run_id"],
                r["from_run_status"],
                r["landed"],
            )
    except Exception as e:
        logger.warning("Stranded-release sweep skipped: %s", e)
    # US-81.2: suite runs stranded the same way hold the per-suite
    # single-flight lock forever — same rule, same moment.
    try:
        reaped = suites_pipeline.reap_orphaned_runs(get_settings())
        if reaped:
            logger.warning("Reaped %d orphaned suite run(s)", reaped)
    except Exception as e:
        logger.warning("Suite-run reaper skipped: %s", e)
    # US-2.15: provider runs stranded by a hard kill of the runner or API
    # must return to a re-dispatchable state, not sit 'running' forever.
    try:
        reaped = db.reap_orphaned_provider_runs(get_settings())
        if reaped:
            logger.warning("Reaped %d orphaned provider run(s)", reaped)
    except Exception as e:
        logger.warning("Provider-run reaper skipped: %s", e)
    # US-26.2: an agent-server job whose task died with the process must not
    # stay 'running' — it holds the one-job-per-host lock forever.
    try:
        reaped = agent_provision.reap_orphaned_jobs(get_settings())
        if reaped:
            logger.warning("Reaped %d orphaned agent server job(s)", reaped)
    except Exception as e:
        logger.warning("Agent-server job reaper skipped: %s", e)
    # US-103.1: a release prep whose supervisor died with the runner process
    # holds the project's only in-flight release slot forever. The restart
    # that orphaned it is very often this one.
    try:
        for r in db.reap_expired_release_preps(get_settings()):
            logger.warning(
                "Reaped abandoned release prep for %s (held by %s for %s min)",
                r["version"],
                r["worker"],
                r["held_minutes"],
            )
    except Exception as e:
        logger.warning("Release-prep reaper skipped: %s", e)
    # US-3.4: expired claims WITH pushed work auto-submit instead of
    # recycling — a pushed-and-forgotten item lands in review.
    try:
        handled = await reconcile.reconcile_pushed_expired_claims(get_settings())
        if handled:
            logger.warning("Auto-submitted %d pushed run(s) with expired claims", handled)
    except Exception as e:
        logger.warning("Push hand-back reconciler skipped: %s", e)
    # US-13.6: the lease sweeps above also run on a timer — an expired
    # claim must surface even when no worker is polling the pool (the
    # lazy sweep) and the process hasn't restarted (the startup sweep).
    async def _liveness_sweep() -> None:
        while True:
            await asyncio.sleep(60)
            try:
                swept = await asyncio.to_thread(db.requeue_expired_claims, get_settings())
                if swept:
                    logger.warning(
                        "Requeued %d expired claim(s) from the sweep", swept
                    )
                await reconcile.reconcile_pushed_expired_claims(get_settings())
                # us-116.4: the presence reaper migration 099 promised. A
                # session whose heartbeat is older than the window is closed
                # so the row agrees with the `live_runner_sessions` view and
                # realtime subscribers see it — a hard-killed API no longer
                # leaves every agent reading online for good.
                stale = await asyncio.to_thread(
                    db.close_stale_runner_sessions, get_settings()
                )
                if stale:
                    logger.warning(
                        "Closed %d runner session(s) with no heartbeat in %ds",
                        stale,
                        db.PRESENCE_WINDOW_SECONDS,
                    )
                # us-116.8: the fleet says when it goes dark — an org that had
                # live agents and has had none for two minutes is told once
                # (managers + a System issue), and the return is recorded.
                from . import fleet_alarm

                dark = await fleet_alarm.fleet_dark_sweep(get_settings())
                if dark.get("opened") or dark.get("closed"):
                    logger.warning(
                        "Fleet-dark sweep: %d org(s) went dark, %d came back",
                        dark.get("opened", 0),
                        dark.get("closed", 0),
                    )
                # US-103.1: release prep's own lease sweep, same loop, same
                # cadence discipline. Its lease is two hours, so this fires
                # long after the agent died — us-103.3's Stop is what covers
                # the window, not a faster tick here.
                for r in await asyncio.to_thread(
                    db.reap_expired_release_preps, get_settings()
                ):
                    logger.warning(
                        "Reaped abandoned release prep for %s (held by %s "
                        "for %s min)",
                        r["version"],
                        r["worker"],
                        r["held_minutes"],
                    )
                # US-119.1: the deploy leg's twin — a `deploying` release
                # whose run is terminal, missing, or past its deployment's
                # own timeout with no live pipeline settles here, on the
                # same cadence, so it cannot sit until the next restart.
                for r in await asyncio.to_thread(
                    deploy.settle_stranded_release_deploys, get_settings()
                ):
                    logger.warning(
                        "Settled stranded release %s: deploy run %s was %s → %s",
                        r["version"],
                        r["run_id"],
                        r["from_run_status"],
                        r["landed"],
                    )
                # US-26.7: agent-server health rides this loop rather than
                # bringing its own scheduler — each host is probed roughly
                # every five minutes, a few at a time.
                await agent_provision.probe_sweep(get_settings())
                # US-68.3: a slot the probe above found failed/inactive gets
                # an escalating auto-repair attempt roughly every 15 minutes,
                # same loop, same cadence discipline.
                await agent_provision.auto_repair_sweep(get_settings())
                # US-57.3 follow-on: drain pool placements queued behind a
                # host's one-job-per-host lock, same loop, same cadence.
                await agent_provision.pool_placement_sweep(get_settings())
                # US-57.18: an agent deleted org-side leaves its machine —
                # the slot is removed and its files purged, asynchronously,
                # because the deletion itself is a browser-side RLS delete
                # no endpoint ever sees.
                await agent_provision.orphan_cleanup_sweep(get_settings())
                # US-59.8: a parked run nobody ever answers or retries closes
                # itself out on the same cadence, through the exact path a
                # manager's own abandon click uses.
                closed = await asyncio.to_thread(db.sweep_unattended_resumable, get_settings())
                if closed:
                    logger.warning(
                        "Abandoned %d unattended resumable run(s) past their TTL",
                        closed,
                    )
                # US-83.3: the CLI-window idle timeout, finally enforced — the
                # UI promised 30 minutes since US-78.10 and nothing kept it.
                from .routers import agent_sessions as agent_sessions_router

                idle_closed = await agent_sessions_router.sweep_idle_sessions(
                    get_settings()
                )
                if idle_closed:
                    logger.warning(
                        "Closed %d idle agent session(s) past the %d-minute timeout",
                        idle_closed,
                        agent_sessions_router.IDLE_TIMEOUT_MINUTES,
                    )
            except Exception as e:  # noqa: BLE001 — the sweep must survive
                logger.warning("Liveness sweep skipped: %s", e)

    sweep_task = asyncio.create_task(_liveness_sweep())
    # US-3.3: the MCP transport's session manager lives with the app. It
    # can only run once per process — a re-entered lifespan (test
    # harnesses) leaves MCP to whichever entry started it first.
    from contextlib import AsyncExitStack

    stack = AsyncExitStack()
    try:
        await stack.enter_async_context(factory_mcp.mcp.session_manager.run())
    except RuntimeError:
        logger.warning("MCP session manager already started; reusing it")
    try:
        async with stack:
            yield
    finally:
        sweep_task.cancel()
        # US-87.6: a clean stop keeps the tail of the buffered request log
        # and hands every pooled connection back to Postgres rather than
        # leaving the pooler to time them out. A hard kill is allowed to
        # lose both — this is a diagnostic table and a reconnect.
        try:
            db.flush_api_request_log(get_settings())
        except Exception:  # noqa: BLE001 — shutdown must not raise
            logger.debug("request-log flush on shutdown failed", exc_info=True)
        try:
            pool.close_all()
        except Exception:  # noqa: BLE001 — shutdown must not raise
            logger.debug("pool close on shutdown failed", exc_info=True)


app = FastAPI(title="Software Factory API", version="0.1.0", lifespan=lifespan)

settings = get_settings()
# US-76.1: websocket-scope exceptions never reach `@app.exception_handler`,
# so they need their own reporter. Outermost, so it sees anything a socket
# route raises past the router.
app.add_middleware(WebSocketErrorReporter)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Worker-Token"],
)

@app.middleware("http")
async def request_timing(request: Request, call_next):
    """US-62.8: total request duration, and how much of it was spent in the
    database — `db._request_db_ms` is a per-request accumulator every
    `with db._connect(...)` block anywhere in the call adds its own elapsed
    time into, via `contextvars` (which `asyncio.to_thread` propagates into
    the executor thread every db.py function actually runs on).

    Logged fire-and-forget after the response is already on its way out —
    measuring must never add latency to the thing being measured.
    """
    start = time.monotonic()
    token = db.begin_request_timing()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    except BaseException as exc:
        if is_client_disconnect(exc):
            # US-79.3: the client closed the request; recording it as a 500
            # would count the caller's hang-up as the app failing.
            status_code = 499
        raise
    finally:
        duration_ms = round((time.monotonic() - start) * 1000)
        db_ms = db.end_request_timing(token)
        route = request.scope.get("route")
        route_path = getattr(route, "path", None) or request.url.path

        async def _log():
            # Fire-and-forget on its own task, whose exception nobody awaits —
            # swallow everything here rather than leave an "exception never
            # retrieved" warning as the only trace, and rather than risk this
            # background write ever being blamed for a request's own failure.
            #
            # Reads `get_settings` through `app.dependency_overrides` — the
            # same lookup a route's own `Depends(get_settings)` would resolve
            # to. Calling the bare function would ignore a test's fake
            # settings entirely and write real request rows into whatever
            # database the real settings point at, for every request any
            # test's TestClient ever makes.
            try:
                settings_factory = app.dependency_overrides.get(get_settings, get_settings)
                await asyncio.to_thread(
                    db.record_api_request,
                    settings_factory(),
                    route_path,
                    request.method,
                    status_code,
                    duration_ms,
                    db_ms,
                )
            except Exception:  # noqa: BLE001
                logger.debug("api request logging failed", exc_info=True)

        asyncio.create_task(_log())


_REPORT_PATH_PREFIX = "/api/v1/report/"


@app.middleware("http")
async def report_endpoint_cors(request: Request, call_next):
    """US-16.2: the ingestion endpoint is called from *other people's* apps,
    running on origins the factory has never heard of, so the configured
    allow-list the rest of the API is protected by would block every report
    before it left the browser.

    Wildcard here is correct rather than lax: this endpoint authenticates on a
    header the caller must already possess, never on the session cookie, so it
    is declared without credentials — a browser will not attach cookies to it,
    and no other endpoint is widened. Added after CORSMiddleware, so it wraps
    it and answers the preflight itself.
    """
    if not request.url.path.startswith(_REPORT_PATH_PREFIX):
        return await call_next(request)

    if request.method == "OPTIONS":
        response = Response(status_code=204)
    else:
        response = await call_next(request)
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Report-Key"
    response.headers["Access-Control-Max-Age"] = "86400"
    # CORSMiddleware stamps this on every response it sees, allowed origin or
    # not. A browser rejects `Allow-Credentials: true` alongside a wildcard
    # origin outright — leaving it would block every report it is meant to
    # permit. Nothing here reads a cookie, so dropping it costs nothing.
    if "access-control-allow-credentials" in response.headers:
        del response.headers["Access-Control-Allow-Credentials"]
    return response


app.include_router(auth.router, prefix="/api/v1")
app.include_router(issues.router, prefix="/api/v1")
app.include_router(projects.router, prefix="/api/v1")
app.include_router(releases.router, prefix="/api/v1")
app.include_router(suite_runs.router, prefix="/api/v1")
app.include_router(github.router, prefix="/api/v1")
app.include_router(reviews.router, prefix="/api/v1")
app.include_router(llm.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")
app.include_router(members.router, prefix="/api/v1")
app.include_router(servers.router, prefix="/api/v1")
app.include_router(agent_servers.router, prefix="/api/v1")
app.include_router(agent_pools.router, prefix="/api/v1")
# Phase 32 agent-level configuration (rename, and the settings that follow it).
app.include_router(agents.router, prefix="/api/v1")
# US-32.5 run-setting presets: org rows and the platform's templates.
app.include_router(presets.router, prefix="/api/v1")
# US-34.1/34.2 the MCP server catalog and the per-run scoped proxy.
app.include_router(mcp_catalog.router, prefix="/api/v1")
app.include_router(deployments.router, prefix="/api/v1")
# US-16.2 app issue ingestion — public, report-key auth, no Supabase session.
app.include_router(app_issues.router, prefix="/api/v1")
app.include_router(notifications.router, prefix="/api/v1")
app.include_router(worker.router, prefix="/api/v1")
app.include_router(workflow.router, prefix="/api/v1")
# US-10.1 supervisor-runner control socket — WS + JSON-RPC at /api/v1/runner/socket.
app.include_router(runner_socket.router, prefix="/api/v1")
# US-78.8: the manager's console onto a live interactive run.
app.include_router(run_console.router, prefix="/api/v1")
# US-78.10: sessions with no work item.
app.include_router(agent_sessions.router, prefix="/api/v1")
# US-10.3 runner LLM gateway — provider-shaped proxy at /api/v1/llm-gateway/*.
app.include_router(llm_gateway.router, prefix="/api/v1")
# US-3.8 factory git remote — git clients hit /git/* at the app root.
app.include_router(gitproxy.router)
# US-3.3 factory MCP server — streamable HTTP at /mcp, worker-token auth.
app.mount("/mcp", factory_mcp.build_mcp_asgi())


class _McpNoRedirect:
    """Serve `/mcp` (no slash) directly instead of 307-redirecting to `/mcp/`.

    2026-08-13: release 2026.08.13.1 failed because the claude CLI's factory
    MCP connection died on that 307 — newer MCP clients refuse to follow
    redirects for MCP servers (a deliberate security behavior; the CLI
    self-updates, so this arrived without any deploy of ours). The agent ran
    toolless and "finished without calling submit_release_notes". Rewriting
    the path here fixes every machine's existing config without an Update.
    Pure ASGI (not BaseHTTPMiddleware) so SSE streaming is untouched.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("path") == "/mcp":
            scope = dict(scope)
            scope["path"] = "/mcp/"
            scope["raw_path"] = b"/mcp/"
        await self.app(scope, receive, send)


app.add_middleware(_McpNoRedirect)


@app.exception_handler(RequestValidationError)
async def redact_request_validation_errors(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """A request body can carry secrets (a pasted PAT, an SSH password/key,
    US-3.15/US-1.28); pydantic v2's `hide_input_in_errors` model config only
    suppresses `input` from `str(exc)`, not from `exc.errors()` — which is
    exactly what FastAPI's default handler serializes into the 422 body. So
    strip `input` from every error entry ourselves before it goes out."""
    errors = [{k: v for k, v in err.items() if k != "input"} for err in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})


@app.exception_handler(StarletteHTTPException)
async def report_operational_http_exception(
    request: Request, exc: StarletteHTTPException
):
    """US-76.1: file the HTTP refusals that are defects, answer all of them
    exactly as before.

    `should_report` is the whole policy — a `ReportedHTTPException` (a
    dependency failed) or any 5xx. Ordinary 4xx passes through silently, which
    is what keeps the console readable.

    The response comes from FastAPI's own handler, not from a reimplementation
    here: status, `detail` body and headers stay byte-identical, so this is
    observability only and cannot change a client contract.
    """
    if should_report(exc):
        report_http_exception(request, exc)
        logger.warning(
            "Reported %s on %s %s: %s",
            exc.status_code,
            scope_method(request),
            request.url.path,
            exc.detail,
        )
    return await http_exception_handler(request, exc)


@app.exception_handler(PostgrestError)
async def answer_postgrest_refusal(
    request: Request, exc: PostgrestError
) -> JSONResponse:
    """BUG-1.1: a query Supabase refused should read like one.

    Deleting a deployment answered `500` with no `detail` for days, because
    the pre-flight read got `300 Multiple Choices` (PGRST201: the embed was
    ambiguous) and nothing between httpx and the browser knew how to say so.
    The dialog could only offer "API error 500", and the operator's only clue
    was a traceback in the crash inbox.

    So: 502 — the request was fine, the upstream refused it — carrying
    PostgREST's code and message, which the UI already renders from `detail`.
    A refusal is still a defect, so it is still self-reported; a handler that
    made the screen nicer and the inbox emptier would be a bad trade.

    Only refusals nobody caught reach here — the call sites that translate
    their own (the 409s in `servers`/`deployments`) still do.
    """
    app_issues_module.self_report(
        get_settings(),
        exc,
        {
            "path": request.url.path,
            "method": scope_method(request),
            "query": str(request.url.query or ""),
            "component": "apps/api",
            "postgrest_code": exc.code or "",
        },
    )
    logger.error(
        "PostgREST refused %s %s: %s",
        scope_method(request),
        request.url.path,
        exc.message,
    )
    named = f" ({exc.code})" if exc.code else ""
    return JSONResponse(
        status_code=502,
        content={"detail": f"The database refused this query{named}: {exc.message}"},
    )


@app.exception_handler(SupabaseUnreachable)
async def answer_supabase_unreachable(
    request: Request, exc: SupabaseUnreachable
) -> JSONResponse:
    """US-79.5 (prod BUG-6): the database not answering is said in words.

    Twice in prod a connect timeout to Supabase climbed out of httpx unhandled:
    the manager saw "Internal Server Error" and the inbox got a raw
    `ConnectTimeout` traceback with no hint of what timed out. Now it is a 504
    whose detail the UI already renders, and the report is titled
    `SupabaseUnreachable` with the operation attached — so five endpoints
    failing the same way dedupe into one legible incident.

    Reads retried once before reaching here (`supabase._send`); writes never —
    a timeout does not prove the write failed, and "nothing was recorded" must
    stay true.
    """
    app_issues_module.self_report(
        get_settings(),
        exc,
        {
            "path": request.url.path,
            "method": scope_method(request),
            "component": "apps/api",
            "operation": exc.operation,
        },
    )
    logger.error(
        "Supabase unreachable during %s (%s %s)",
        exc.operation,
        scope_method(request),
        request.url.path,
    )
    return JSONResponse(
        status_code=504,
        content={
            "detail": f"the database did not answer ({exc.cause}) — "
            "nothing was recorded; try again"
        },
    )


@app.exception_handler(Exception)
async def report_unhandled_exception(request: Request, exc: Exception) -> Response:
    """US-16.8: an error nobody caught is a system error, so it is recorded.

    Only genuinely unhandled exceptions reach here — an `HTTPException` (every
    4xx, and the deliberate 409/422 the pipeline raises) is handled by
    Starlette before this and is pipeline state, not a defect.

    The report is best-effort in the strictest sense: `self_report` swallows
    everything, including its own failure, so a broken reporting path can never
    convert a 500 into something worse. The client's 500 is unchanged either
    way.
    """
    if is_client_disconnect(exc):
        # US-79.3 (prod BUG-4): the caller hung up mid-request — bare or
        # wrapped in a one-member ExceptionGroup by the middleware stack.
        # Nothing failed here, and nobody is listening for this response;
        # a report row would be noise in the exact inbox US-16.8 built.
        logger.debug("client disconnected mid-request on %s", request.url.path)
        return Response(status_code=204)
    app_issues_module.self_report(
        get_settings(),
        exc,
        {
            "path": request.url.path,
            "method": scope_method(request),
            "query": str(request.url.query or ""),
            "component": "apps/api",
        },
    )
    logger.exception(
        "Unhandled exception on %s %s", scope_method(request), request.url.path
    )
    return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})


@app.get("/api/v1/health")
async def health():
    return {"status": "ok", **_build_stamp()}


# US-91.16: the same stamp the web footer reads, so "are web and api the same
# build" is answerable without SSH. Written by the deploy workflow to
# apps/web/VERSION; the API reads that one file rather than keeping a second
# copy that could disagree with it.
_BUILD_STAMP: dict[str, str] | None = None


def _build_stamp() -> dict[str, str]:
    global _BUILD_STAMP
    if _BUILD_STAMP is None:
        stamp: dict[str, str] = {}
        try:
            path = (
                pathlib.Path(__file__).resolve().parents[3] / "apps" / "web" / "VERSION"
            )
            for line in path.read_text(encoding="utf-8").splitlines():
                key, sep, value = line.partition("=")
                if sep and value.strip():
                    stamp[key.strip()] = value.strip()
        except OSError:
            # A checkout without the stamp is a dev build, not an error.
            pass
        _BUILD_STAMP = {
            "build_version": stamp.get("version", ""),
            "build_commit": stamp.get("commit", ""),
            "build_ref": stamp.get("ref", ""),
            "built_at": stamp.get("built_at", ""),
        }
    return _BUILD_STAMP
