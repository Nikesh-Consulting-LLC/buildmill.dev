"""Entrypoint: run the supervisor runner.

    python -m supervisor            # from apps/runner/

Env:
    FACTORY_API_URL       default http://localhost:8000
    FACTORY_WORKER_TOKEN  minted on Settings → Workers (US-3.1)

Holds the control socket (config/relay/audit) AND the work loop (pull → claim →
supervise → submit) concurrently. Which modules and models it uses is decided
server-side (US-10.2); no model secrets live on this machine (US-10.3).
"""

import asyncio
import logging
import os
import sys

from . import mcp_broker, modules, session_host, session_input, workspace_prepare
from .connection import RunnerConnection
from .workloop import Supervisor, WorkerClient, model_env, subscription_env, subscription_mode


async def _run(api_url: str, token: str) -> None:
    # US-89.1: the loopback MCP broker — every CLI's factory MCP config points
    # at 127.0.0.1 with a machine-local key, and the worker token is injected
    # here, at forward time, never written into a workspace file. Best-effort:
    # if it cannot bind, mcpconfig falls back to the legacy shape.
    await mcp_broker.start(api_url)
    conn = RunnerConnection(
        api_url=api_url,
        token=token,
        modules_available=modules.available(),
        # US-32.4: and what each of them accepts.
        module_settings=modules.declarations(),
    )
    # Manager-triggered "prepare codebase" test — server→runner requests
    # this connection doesn't otherwise handle.
    # US-78.8: `session.input` joins it — the manager's text, on its way to a
    # live ACP session. Each handler ignores every method but its own, so they
    # compose without knowing about each other.
    async def _on_message(msg):
        await workspace_prepare.handle(conn, msg)
        await session_input.handle(conn, msg)
        # US-78.10: a session with no work item, opened directly.
        await session_host.handle(conn, msg)

    conn.on_message = _on_message
    client = WorkerClient(api_url, token)
    gateway_base = f"{api_url.rstrip('/')}/api/v1/llm-gateway"

    async def env_provider(run_id, kind, module, resolved=None):
        provider_type = getattr(module, "provider_type", "")
        if not provider_type:
            return {}  # e.g. the sim module needs no gateway
        # US-32.8: the model the factory resolved for THIS run, which is what
        # the gateway keys on to pick a provider (us-27.8). The pre-preset
        # `model_routes` value is the fallback for an older server.
        model = (resolved or {}).get("model") or (
            conn.config.get("model_routes") or {}
        ).get(kind, "")
        # US-52.1: a subscription run gets NO gateway env — no mint, no
        # ANTHROPIC_API_KEY, no base URL. The API-key variable outranks every
        # subscription credential in Claude Code's chain, so billing the
        # subscription means not injecting it; the CLI inherits
        # CLAUDE_CODE_OAUTH_TOKEN or the machine's login state from os.environ.
        # US-52.2: unless the factory holds a token — then it rides in and
        # outranks the machine's own, being the one the manager can see.
        # US-53.1: the switch is the AGENT's config, not a resolved setting.
        if subscription_mode(module, conn.config):
            return subscription_env(model, await conn.fetch_subscription_token())
        # US-27.8: the model rides the mint, so the gateway forwards to the
        # provider that offers it rather than to whatever the org default is.
        key = await conn.mint_gateway_key(
            run_id=run_id, route=f"runner_{kind}", model=model
        )
        # US-78.5: the module name decides the variable names, not just the
        # provider type — two different programs speak `xai` here and read
        # different ones.
        return model_env(
            provider_type,
            gateway_base,
            key,
            model,
            module=getattr(module, "name", ""),
        )

    sup = Supervisor(
        client,
        config_provider=lambda: conn.config,
        env_provider=env_provider,
        connection=conn,
    )
    stop = asyncio.Event()
    await asyncio.gather(conn.run_forever(stop), sup.supervise(stop))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="[supervisor] %(message)s")
    api_url = os.environ.get("FACTORY_API_URL", "http://localhost:8000")
    token = os.environ.get("FACTORY_WORKER_TOKEN", "")
    if not token:
        sys.exit("FACTORY_WORKER_TOKEN is not set — mint one on Settings → Workers")
    # us-96.11: the long-lived credential this process holds, registered
    # before anything can emit — no telemetry leaves the box carrying it.
    from . import redact

    redact.register("worker-token", token)
    try:
        asyncio.run(_run(api_url, token))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
