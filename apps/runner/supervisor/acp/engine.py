"""One way to open an ACP session (US-83.2).

Two owners used to carry their own copy of this sequence — `interactive`'s
`_run_cli` for dispatched runs, `session_host._open` for CLI-window sessions —
and the copies drifted exactly the way two copies do: the run path gained the
handshake trace line and the tools-refusal, the session path had neither. The
whole stretch from spawn to a usable session id now lives here, once, and a
fix to it is a fix to both owners.

What stays with the owners, deliberately: the model-config write (each owner
sources its gateway env differently), prompting, outcome scoring, and LIVE
registration — those are what MAKE one a run and the other a conversation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .client import AcpClient, AcpError
from .handlers import ClientHandlers
from .mcp import servers_from_config_file

# Sync on purpose: the run path's progress sink is sync, and US-83.2 AC4 makes
# the session path's sync too (an enqueue) — narration must never be able to
# hold the ACP read loop hostage to a slow socket.
Emit = Callable[[str, str], None]


@dataclass
class OpenedSession:
    """A live session, ownership transferred to the caller — including the
    duty to close `client` and `proc` when done."""

    proc: Any
    client: AcpClient
    session_id: str
    resumed: bool


async def open_session(
    prim: Any,
    argv: list[str],
    cwd: str,
    *,
    mcp_config: str | None = None,
    resume_session_id: str | None = None,
    emit: Emit,
    on_update: Callable[[dict], Awaitable[None]] | None = None,
    on_permission: Any = None,
    on_disconnect: Callable[[], None] | None = None,
    env: dict[str, str] | None = None,
) -> OpenedSession:
    """Spawn the agent, handshake, hand it its tools, resume or start a session.

    `on_update` receives each `session/update`'s params — except while a
    `session/load` replay is in flight. The mute gates EVERYTHING, not just
    narration: before US-83.2 the run path collected answer chunks during the
    replay, so a resumed run's "final answer" carried the prior conversation's
    text prepended (US-78.9's latent leak, fixed by construction here).

    On any failure the spawned process is closed before the error propagates —
    a session that failed to open must not leak a child. The child's stderr
    tail rides the error, because "initialize did not answer" without the
    binary's own words is a puzzle, not a report.

    us-115.1: `env` overlays the child's environment. A CLI that reads its MCP
    servers from its own config file needs two things there that cannot ride
    argv — the credential its config points at by name, and
    `MCP_INIT_STRATEGY`, which the CLI checks before any hint and which is what
    keeps the first LLM turn from starting while a server is still handshaking.
    """
    muted = {"value": False}

    async def _notify(method: str, params: dict) -> None:
        if method != "session/update" or muted["value"]:
            return
        if on_update is not None:
            await on_update(params)

    proc = await prim.run_session(argv, cwd=str(cwd), env=env or None)
    handlers = ClientHandlers(
        prim, str(cwd), roots=[str(cwd)], on_permission=on_permission
    )
    client = AcpClient(
        proc,
        on_request=handlers,
        on_notification=_notify,
        on_disconnect=on_disconnect,
    )
    client.start()
    try:
        await client.initialize()
        transports = client.mcp_transports()
        # US-78.1/78.4/78.9: what the agent SAYS it can do, on the record.
        # `loadSession` and HTTP MCP were inferred from third-party
        # integrations for the whole build; a line per session means nobody
        # has to infer them again, and a CLI upgrade that drops a capability
        # shows up in the trace instead of as a puzzling failure later.
        info = client.agent_info or {}
        emit(
            "step",
            "agent handshake — "
            + " · ".join(
                [
                    f"{info.get('name') or 'unknown'} {info.get('version') or ''}".strip(),
                    f"protocol {client.protocol_version}",
                    f"resume {'yes' if client.supports_load_session else 'no'}",
                    "mcp "
                    + (", ".join(k for k, v in transports.items() if v) or "none"),
                ]
            ),
        )
        servers, notes = servers_from_config_file(mcp_config, transports)
        for note in notes:
            emit("step", f"tools: {note}")
        if mcp_config and not servers:
            # US-78.4 AC5, now on BOTH owners: an agent that starts without
            # its tools burns a model budget to discover it cannot do the job.
            raise AcpError(
                "the factory's MCP tools could not be given to this agent — "
                + ("; ".join(notes) or "no servers could be configured")
            )

        session_id: str | None = None
        resumed = False
        if resume_session_id and client.supports_load_session:
            # US-78.9: `session/load` replays the entire conversation as
            # updates before it returns. Muted, or the trace is written twice.
            muted["value"] = True
            try:
                await client.session_load(str(resume_session_id), str(cwd), servers)
                session_id = str(resume_session_id)
                resumed = True
                emit("step", "resumed the earlier session")
            except AcpError as e:
                emit("step", f"could not resume ({e}) — starting a new session")
            finally:
                muted["value"] = False
        elif resume_session_id:
            emit(
                "step",
                "this agent does not support resuming a session — starting a new one",
            )
        if session_id is None:
            session_id = await client.session_new(str(cwd), servers)
        return OpenedSession(
            proc=proc, client=client, session_id=session_id, resumed=resumed
        )
    except Exception as e:
        tail = ""
        try:
            tail = proc.stderr_tail() or ""
        except Exception:  # noqa: BLE001 — diagnostics must not mask the error
            pass
        await client.close()
        await proc.close()
        if tail and isinstance(e, AcpError):
            raise AcpError(f"{e.message}\n{tail}", code=e.code, data=e.data) from e
        raise
