"""Errors that are worth telling the superadmin about (US-76.1).

US-16.8 files *unhandled* exceptions into the System issues inbox. That set is
smaller than "errors users hit": a router that catches a failing dependency and
translates it into a deliberate `HTTPException` produces a perfectly ordinary
4xx, which Starlette resolves long before the catch-all in `main` can see it.
So "GitHub merge failed" reached a manager's screen and nothing reached the
console.

The fix is not "report every 4xx" — a console listing every permission denial
is one nobody opens. Status code cannot separate the two cases (a 409 is
"someone else claimed this run" *and* "GitHub refused the merge"), so the raise
site says which it is by choosing the exception class.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import HTTPException
from starlette.requests import ClientDisconnect
from starlette.websockets import WebSocketDisconnect

from . import app_issues as app_issues_module
from .config import get_settings

logger = logging.getLogger(__name__)


def unwrap(exc: BaseException) -> BaseException:
    """Peel single-member ExceptionGroups down to the failure itself.

    Starlette's collapsing task group re-raises what happened inside a
    `BaseHTTPMiddleware` stack sometimes bare and sometimes as a one-member
    group (prod BUG-4 arrived both ways). Decisions about an exception — and
    report titles — should be about the failure, not the wrapper. A group with
    several members is left intact: there is no single "the" failure to name.
    """
    while isinstance(exc, BaseExceptionGroup) and len(exc.exceptions) == 1:
        exc = exc.exceptions[0]
    return exc


def is_client_disconnect(exc: BaseException) -> bool:
    """US-79.3: a caller that hung up mid-request is not a defect."""
    return isinstance(unwrap(exc), ClientDisconnect)


class ReportedHTTPException(HTTPException):
    """An `HTTPException` whose cause is a failing dependency, not the caller.

    Behaves exactly like `HTTPException` on the wire — same status, same
    `detail`, same headers — and additionally files a System issues report.
    `context` is merged into the report so the raise site can attach what it
    knows (the PR it could not merge, the run it was for).
    """

    def __init__(
        self,
        status_code: int,
        detail: Any = None,
        headers: dict[str, str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.context = context or {}


def should_report(exc: HTTPException) -> bool:
    """Which HTTP refusals are defects.

    A `ReportedHTTPException` says so itself. Anything 5xx is a defect by
    definition — the request was fine and we failed it — whether it was raised
    deliberately or not. Everything else is pipeline state: unauthorized,
    forbidden, not found, already claimed, unprocessable.
    """
    return isinstance(exc, ReportedHTTPException) or exc.status_code >= 500


def scope_method(request) -> str:
    """`request.method`, surviving every scope a handler can be invoked for.

    Starlette hands an exception handler whichever connection object the scope
    produced — for a websocket route that is a `WebSocket`, which has no
    `.method`. Dereferencing it raised AttributeError from *inside* the error
    path, so the inbox reported the mask instead of the cause, six copies deep
    (prod BUG-8). Every handler (and helper a handler calls) resolves the
    method through here; `test_handler_scopes` sweeps them all.
    """
    return getattr(request, "method", "WEBSOCKET")


def report_http_exception(request, exc: HTTPException) -> None:
    """File one HTTP refusal. Never raises — `self_report` swallows its own
    failures, and this must not turn a handled 409 into an unhandled 500."""
    context: dict[str, Any] = {
        "path": request.url.path,
        "method": scope_method(request),
        "query": str(request.url.query or ""),
        "component": "apps/api",
        "status_code": exc.status_code,
    }
    extra = getattr(exc, "context", None)
    if isinstance(extra, dict):
        context.update(extra)
    app_issues_module.self_report(get_settings(), exc, context)


async def safe_accept(websocket) -> bool:
    """Accept a websocket, treating a peer that already vanished as a hang-up.

    A peer that goes away between the route match and `accept()` leaves uvicorn
    answering the accept with a bare RuntimeError ("Expected ASGI message
    'websocket.send' or 'websocket.close', but got 'websocket.accept'."). The
    app cannot prevent that race, only survive it — and it recurs on every
    deploy restart and tunnel drop, because the runner reconnects on a ≤30s
    backoff the whole time the API is coming back up. 61 of these in 28 hours
    were the single noisiest report in the inbox (prod BUG-7).

    Returns False on that ending (and on a plain disconnect); the route should
    simply return. Any other failure still raises. `test_ws_accept` pins that
    no route calls `accept()` bare anymore.
    """
    try:
        await websocket.accept()
        return True
    except WebSocketDisconnect:
        pass
    except RuntimeError as exc:
        # Matched on uvicorn's own wording — a bare RuntimeError offers
        # nothing else to key on. Deliberately narrow, same as the reporter
        # exemption below.
        if not str(exc).startswith("Expected ASGI message"):
            raise
    logger.debug(
        "websocket peer hung up before accept on %s",
        getattr(getattr(websocket, "url", None), "path", ""),
    )
    return False


class WebSocketErrorReporter:
    """Report exceptions raised inside a WebSocket, then re-raise them.

    `@app.exception_handler(Exception)` is HTTP-only: Starlette does not route
    websocket-scope exceptions to it, and a handler returning a `JSONResponse`
    would be meaningless for a socket anyway. So the runner control socket and
    the live console could crash all day and leave nothing but a traceback in
    journald.

    Pure ASGI rather than `BaseHTTPMiddleware`, which only sees HTTP scopes.
    The exception is always re-raised: uvicorn's logging and the socket's close
    behavior stay exactly as they were.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "websocket":
            await self.app(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        except (WebSocketDisconnect, asyncio.CancelledError):
            # A client hanging up is how a socket normally ends. Reporting it
            # would bury the crashes this class exists to surface.
            raise
        except RuntimeError as exc:
            # The same hang-up, one moment earlier. A peer that vanishes DURING
            # the handshake leaves the server calling `accept()` on a
            # connection the ASGI layer has already finished with, and uvicorn
            # answers "Expected ASGI message 'websocket.send' or
            # 'websocket.close', but got 'websocket.accept'" — a RuntimeError,
            # so it misses the exemption above and lands in the inbox as a
            # defect. Observed once, on the runner's control socket, at the
            # moment a deploy restarted the API: expected, and it will recur on
            # every release.
            #
            # Matched on the message because uvicorn raises a bare RuntimeError
            # with nothing else to key on. Deliberately narrow: any other
            # RuntimeError is a real crash and still reported.
            if not str(exc).startswith("Expected ASGI message"):
                app_issues_module.self_report(
                    get_settings(),
                    exc,
                    {
                        "path": scope.get("path", ""),
                        "component": "apps/api",
                        "transport": "websocket",
                    },
                )
            raise
        except Exception as exc:
            app_issues_module.self_report(
                get_settings(),
                exc,
                {
                    "path": scope.get("path", ""),
                    "component": "apps/api",
                    "transport": "websocket",
                },
            )
            raise
