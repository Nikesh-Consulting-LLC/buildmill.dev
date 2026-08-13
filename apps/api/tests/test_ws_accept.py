"""US-79.6: a hang-up before accept is a hang-up (prod BUG-7).

uvicorn answers `accept()` on a connection the peer already abandoned with a
bare RuntimeError. 9f9d09a stopped the *reporting*; this stops the raise at
every route, through one shared `safe_accept`, and pins that no route ever
calls `accept()` bare again.
"""

import asyncio
import re
from pathlib import Path

import pytest
from starlette.websockets import WebSocketDisconnect

from app.errors import safe_accept

_ASGI_RACE = RuntimeError(
    "Expected ASGI message 'websocket.send' or 'websocket.close', "
    "but got 'websocket.accept'."
)


class _Socket:
    def __init__(self, raises: BaseException | None = None):
        self.raises = raises
        self.accepted = False

    async def accept(self):
        if self.raises is not None:
            raise self.raises
        self.accepted = True


def test_a_vanished_peer_is_a_quiet_false():
    ws = _Socket(raises=_ASGI_RACE)
    assert asyncio.run(safe_accept(ws)) is False


def test_a_disconnect_during_accept_is_a_quiet_false():
    ws = _Socket(raises=WebSocketDisconnect(code=1006))
    assert asyncio.run(safe_accept(ws)) is False


def test_any_other_runtime_error_still_raises():
    ws = _Socket(raises=RuntimeError("deliberate: the loop is gone"))
    with pytest.raises(RuntimeError, match="the loop is gone"):
        asyncio.run(safe_accept(ws))


def test_a_normal_accept_answers_true():
    ws = _Socket()
    assert asyncio.run(safe_accept(ws)) is True
    assert ws.accepted


def test_no_route_calls_accept_bare():
    """The whole point: the race can only be survived where it happens, so
    every `websocket.accept()` in the app must go through `safe_accept` —
    the reporter exemption (9f9d09a) only silences the inbox, not the raise.
    A new socket route reintroducing a bare accept fails here by name."""
    app_dir = Path(__file__).resolve().parents[1] / "app"
    bare = []
    for py in sorted(app_dir.rglob("*.py")):
        if py.name == "errors.py":  # safe_accept itself owns the one real call
            continue
        for lineno, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"await\s+\w+\.accept\(\)", line):
                bare.append(f"{py.relative_to(app_dir)}:{lineno}: {line.strip()}")
    assert bare == [], (
        "bare websocket.accept() calls found — route them through "
        "app.errors.safe_accept (US-79.6):\n" + "\n".join(bare)
    )
