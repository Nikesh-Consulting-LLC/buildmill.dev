"""Runner LLM gateway (US-10.3).

Two consumers, one Vault-keyed brain behind the server:
  * the supervisor BRAIN reasons via the `llm.infer` socket relay (in
    runner_socket.py) — server-side, no key on the machine;
  * CLI agent MODULES point their provider SDK at this gateway with a
    short-lived scoped key (`llm_gateway_keys`); the gateway swaps in the org's
    real provider key from Vault and forwards to the provider.

No provider key ever reaches the runner machine. Provider selection reuses the
US-3.17 routing (`llm._targets_for`) so a `route` maps to a provider + model.
"""

import asyncio
import logging
import time

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from starlette.requests import ClientDisconnect

from .. import db, llm, metering
from ..config import Settings, get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/llm-gateway", tags=["llm-gateway"])

# Default API bases by provider_type (used when llm_providers.base_url is unset).
_PROVIDER_BASE = {
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com",
    "xai": "https://api.x.ai",
    "groq": "https://api.groq.com/openai",
}

# Client headers safe to forward upstream (never the incoming auth header).
_FORWARD_HEADERS = {"content-type", "accept", "anthropic-version", "anthropic-beta"}


def module_env(
    provider_type: str, gateway_base: str, key: str, model: str, module: str = ""
) -> dict[str, str]:
    """Env a CLI module needs so its provider SDK hits the gateway instead of
    the real provider; the scoped key stands in for the provider key."""
    base = gateway_base.rstrip("/")
    pt = provider_type.lower()
    if module == "interactive":
        # US-78.5: the Buildmill Interactive Agent's CLI (a fork of
        # xai-org/grok-build) reads a DIFFERENT pair from the Grok Build module
        # above, because it is a different program: `GROK_MODELS_BASE_URL` is
        # its documented custom-inference-endpoint override, and the key rides
        # under a named env var referenced from its config.toml `env_key`
        # (its docs prefer `env_key` over an inline `api_key`, and so do we —
        # an inline key would be written to disk).
        #
        # `GROK_XAI_API_BASE_URL` is set alongside it because the CLI falls
        # back to that for anything not routed by the model block; leaving it at
        # the default would let a request slip past the gateway to api.x.ai.
        #
        # No `auth.json` may exist in this agent's GROK_HOME (US-78.1): a login
        # session outranks the key and would take the run off-meter.
        env = {
            "GROK_MODELS_BASE_URL": f"{base}/v1",
            "GROK_XAI_API_BASE_URL": f"{base}/v1",
            "BUILDMILL_GATEWAY_KEY": key,
            "GROK_MODEL": model,
            # US-78.5: ACP mode has no `-m` flag, so the default is the
            # only way to say which model a session reasons with.
            "GROK_DEFAULT_MODEL": model,
        }
        # US-83.1: measured off the CLI's own handshake catalog (mirrored in
        # workloop.KNOWN_CONTEXT_WINDOWS — extend both, from measurement only).
        if model == "grok-4.5":
            env["GROK_MODEL_CONTEXT_WINDOW"] = "500000"
        return env
    if pt == "anthropic":
        return {
            "ANTHROPIC_BASE_URL": base,
            "ANTHROPIC_API_KEY": key,
            "ANTHROPIC_MODEL": model,
        }
    if pt == "xai":
        # US-10.5 follow-up, measured against the CLI's two most recent
        # generations (1.0.0 then 1.1.7 -- its own release channel moved
        # between them mid-story): GROK_API_KEY/GROK_BASE_URL/GROK_MODEL is
        # what 1.1.7 actually reads, confirmed live -- the scoped key alone
        # under GROK_API_KEY with no base URL override sent it straight to
        # the real api.x.ai, which rejected it as an invalid key ("XAI_*" was
        # this module's first guess and is dead weight, kept only in case a
        # future CLI version reads it instead). Like OpenCode's OpenAI-SDK
        # base URL, the CLI's own SDK appends `/chat/completions` straight
        # onto whatever base it's given, so `/v1` has to be added here.
        return {
            "GROK_API_KEY": key,
            "GROK_BASE_URL": f"{base}/v1",
            "GROK_MODEL": model,
        }
    if pt in ("openai", "groq"):
        return {
            "OPENAI_BASE_URL": base,
            "OPENAI_API_KEY": key,
            "OPENAI_MODEL": model,
        }
    return {"LLM_BASE_URL": base, "LLM_API_KEY": key, "LLM_MODEL": model}


def _auth_headers(provider_type: str, provider_key: str) -> dict[str, str]:
    if provider_type.lower() == "anthropic":
        return {"x-api-key": provider_key}
    return {"Authorization": f"Bearer {provider_key}"}


def _trace(settings: Settings, claims: dict, message: str) -> None:
    """US-27.8: say which provider answered, in the run's own trace.

    The 2026-07-26 failure was unreadable precisely because the routing
    decision left no record anywhere: the run said the model might not exist,
    and nothing said which provider had been asked. Best-effort — a gateway
    request must never fail because a trace could not be written."""
    run_id = claims.get("run_id")
    worker_id = claims.get("worker_id")
    if not (run_id and worker_id):
        return
    try:
        db.record_run_trace(settings, str(run_id), str(worker_id), "note", message)
    except Exception:  # noqa: BLE001
        logger.debug("gateway trace not recorded", exc_info=True)


async def _meter_call(
    settings: Settings,
    claims: dict,
    provider: dict,
    model: str,
    meter: "metering.UsageMeter",
    status_code: int,
    started_at: float,
) -> None:
    """US-33.1: one usage row for the call that just finished.

    Every failure mode — a parse that found nothing, a database that refused the
    write, a payload nobody anticipated — is logged and swallowed. The gateway's
    job is to relay; a run must never fail because its cost could not be
    recorded.

    US-62.3: `started_at` is a `time.monotonic()` reading taken right before
    the upstream request was sent — before this call existed, `llm_usage` had
    no latency at all, only tokens and cost. Measured to here (stream fully
    drained), not to the first byte, so it answers "how long did the run
    actually wait on this call" rather than only time-to-first-byte.
    """
    try:
        meter.finish()
        row = meter.as_row()
        latency_ms = round((time.monotonic() - started_at) * 1000)
        org_id = str(claims["org_id"])
        rates = {}
        try:
            rates = db.model_prices(settings, org_id).get(model, {})
        except Exception:  # noqa: BLE001 — an unpriced row is still a row
            logger.debug("model prices unavailable while metering", exc_info=True)
        rate_in = rates.get("input_per_mtok")
        rate_out = rates.get("output_per_mtok")
        # US-38.1: None when unset, and cost_for then charges those tokens at
        # rate_in -- today's behaviour exactly, so no bill silently drops.
        rate_cache_read = rates.get("cache_read_per_mtok")
        rate_cache_write = rates.get("cache_write_per_mtok")
        usage = {
            "org_id": org_id,
            "run_id": claims.get("run_id"),
            # US-83.2: a session key stamps its session the way a run key
            # stamps its run — the Phase 78 column finally has its writer.
            "session_id": claims.get("session_id"),
            "worker_id": claims.get("worker_id"),
            "project_id": claims.get("project_id"),
            "provider_id": provider.get("id"),
            "provider_type": provider.get("provider_type"),
            "provider_name": provider.get("name"),
            "model": model,
            "route": claims.get("route"),
            "rate_in_per_mtok": rate_in,
            "rate_out_per_mtok": rate_out,
            "rate_cache_read_per_mtok": rate_cache_read,
            "rate_cache_write_per_mtok": rate_cache_write,
            "cost_usd": metering.cost_for(
                row["tokens_in"],
                row["tokens_out"],
                rate_in,
                rate_out,
                cache_read=row.get("cache_read_tokens"),
                cache_write=row.get("cache_write_tokens"),
                rate_cache_read=rate_cache_read,
                rate_cache_write=rate_cache_write,
            ),
            "status_code": status_code,
            "latency_ms": latency_ms,
            **row,
        }
        await asyncio.to_thread(db.record_llm_usage, settings, usage)
        if not row["parsed"]:
            logger.info(
                "gateway usage unparsed for run %s (%s/%s): %s",
                claims.get("run_id"),
                provider.get("provider_type"),
                model,
                row["parse_note"],
            )
    except Exception:  # noqa: BLE001 — never into the relay
        logger.warning("gateway metering failed", exc_info=True)


class NoProviderForModel(Exception):
    """US-27.8: the key names a model no configured provider offers."""

    def __init__(self, model: str, offered: list[str]):
        super().__init__(model)
        self.model = model
        self.offered = offered


def provider_for_model(providers: list[dict], model: str) -> dict | None:
    """The configured provider whose `models` contains this id.

    US-27.8: a model id belongs to exactly one configured provider, so the
    agent's own configuration is enough to say who should answer it. This is
    what makes `claude-sonnet-5` reach Anthropic while the org default points
    at Groq — one decision instead of two invisible ones that can disagree."""
    wanted = (model or "").strip()
    if not wanted:
        return None
    for provider in providers:
        if wanted in (provider.get("models") or []):
            return provider
    return None


def _resolve_platform_provider(settings: Settings, requested_model: str | None):
    """US-60.1: Buildmill Agent never resolves against the org's own
    configured providers — it always speaks to the ONE platform-owned
    Anthropic key. Same return shape as `_resolve_provider` so the rest of
    `proxy()` (base/key checks, metering) needs no branch of its own."""
    entry = db.get_platform_llm_key(settings)
    model = (requested_model or "").strip() or (entry or {}).get("model") or "claude-sonnet-5"
    provider = {"id": None, "provider_type": "anthropic", "name": "Buildmill Agent"}
    key = (entry or {}).get("key") if entry else None
    base = _PROVIDER_BASE["anthropic"]
    return provider, model, key, base


def _resolve_provider(
    settings: Settings, org_id: str, route: str, model: str | None = None
):
    """The provider + model + key + base a request forwards to.

    With a model (a CLI module's key, US-27.8) the provider is the one that
    offers that model. Without one — the brain, and any key minted before this
    shipped — it is the US-3.17 route lookup with the org default behind it,
    unchanged."""
    providers, routes = db.get_org_llm_config(settings, org_id)
    if (model or "").strip():
        provider = provider_for_model(providers, model or "")
        if not provider:
            raise NoProviderForModel(
                (model or "").strip(),
                sorted(
                    {m for p in providers for m in (p.get("models") or [])}
                ),
            )
        resolved_model = (model or "").strip()
    else:
        provider, resolved_model = llm._targets_for(route, providers, routes)[0]
    key = None
    if provider.get("vault_secret_id"):
        key = llm.read_vault_secret(settings, provider["vault_secret_id"])
    base = provider.get("base_url") or _PROVIDER_BASE.get(provider["provider_type"])
    return provider, resolved_model, key, base


@router.api_route("/{path:path}", methods=["POST", "GET"])
async def proxy(
    path: str,
    request: Request,
    settings: Settings = Depends(get_settings),
    x_api_key: str = Header(default=""),
    authorization: str = Header(default=""),
):
    """Forward a provider-shaped request to the org's real provider, keyed from
    Vault. Authenticated only by a valid scoped gateway key."""
    scoped = x_api_key or (
        authorization[7:] if authorization.lower().startswith("bearer ") else ""
    )
    claims = db.validate_gateway_key(settings, scoped)
    if not claims:
        raise HTTPException(status_code=401, detail="invalid or expired gateway key")

    # US-37.2: there is deliberately no spend check here. Money is now bounded
    # per PROJECT, at the one place a run is created (migration 164's trigger),
    # not per call mid-run. Refusing a call in flight is what stopped tasks at
    # 90% for a limit nobody set on anything they think about; the project
    # budget refuses to START work instead, and raising it resumes everything.

    try:
        if claims.get("platform_billed"):
            provider, _model, provider_key, base = _resolve_platform_provider(
                settings, claims.get("model")
            )
        else:
            provider, _model, provider_key, base = _resolve_provider(
                settings, str(claims["org_id"]), claims["route"], claims.get("model")
            )
    except NoProviderForModel as e:
        _trace(
            settings,
            claims,
            f"gateway: no configured provider offers '{e.model}' — the request "
            "was refused rather than sent to a provider that cannot answer it",
        )
        # US-27.8: distinct from "no key configured". The 2026-07-26 failure
        # said the model might not exist when the truth was that the request
        # had been handed to a provider that does not offer it.
        raise HTTPException(
            status_code=502,
            detail=(
                f"no configured provider offers the model '{e.model}' — add it "
                "to a provider in Settings → LLM providers, or route this "
                "agent to a model one of them has"
                + (f" ({', '.join(e.offered[:8])})" if e.offered else "")
            ),
        )
    except llm.LlmNotConfigured:
        raise HTTPException(
            status_code=502, detail="no LLM provider configured for this route"
        )
    if not base or (not provider_key and provider["provider_type"] != "ollama"):
        # US-60.1: a Buildmill Agent org cannot set the platform's own key —
        # telling it to visit Settings → LLM providers would be wrong.
        detail = (
            "Buildmill Agent has no platform key configured yet — the "
            "superadmin needs to set one"
            if claims.get("platform_billed")
            else f"the provider '{provider.get('name') or provider['provider_type']}' "
            "has no API key configured — set one in Settings → LLM providers"
        )
        raise HTTPException(status_code=502, detail=detail)
    if not (claims.get("model") or "").strip():
        # No model on the key: the brain, or a key minted before US-27.8. The
        # route decided, which for a `runner_<kind>` route means the org
        # default decided. Worth saying out loud — it is the exact shape of
        # the 2026-07-26 failure.
        _trace(
            settings,
            claims,
            f"gateway: route '{claims['route']}' carried no model, so the org's "
            f"default provider ({provider.get('name') or provider['provider_type']}) "
            f"answered with {_model}",
        )

    try:
        body = await request.body()
    except ClientDisconnect:
        # US-79.3 (prod BUG-4): the caller hung up mid-upload. Nothing failed
        # here and nobody is listening for an answer — the 204 goes nowhere.
        # Guarded at the source as well as in the catch-all, so a middleware
        # reshuffle cannot silently turn hang-ups back into crash reports.
        logger.debug("gateway caller disconnected mid-request")
        return Response(status_code=204)
    # US-33.1: an OpenAI-shaped provider reports no usage at all on a stream
    # unless it is asked to. Asking is the difference between metering that
    # works and a table full of honest, useless "unparsed" rows. Never mangles:
    # anything it cannot confidently rewrite is forwarded untouched.
    body, usage_requested = metering.ensure_usage_requested(
        provider["provider_type"], body
    )
    upstream = f"{base.rstrip('/')}/{path}"
    fwd = {
        k: v for k, v in request.headers.items() if k.lower() in _FORWARD_HEADERS
    }
    fwd.update(_auth_headers(provider["provider_type"], provider_key or ""))
    # The 2026-07-27 failure: with no accept-encoding of our own, httpx asked
    # for gzip, the provider obliged, aiter_raw() relayed the compressed bytes,
    # and the response below carried no content-encoding — the caller parsed
    # binary as JSON and the meter read none of it. Identity is the only
    # encoding the caller and the meter can both always read.
    fwd["accept-encoding"] = "identity"
    if usage_requested:
        # httpx sets Content-Length from the content we hand it; a stale one
        # forwarded from the client would truncate the rewritten body.
        fwd.pop("content-length", None)

    client = httpx.AsyncClient(timeout=None)
    req = client.build_request(
        request.method, upstream, content=body, headers=fwd, params=request.query_params
    )
    # US-62.3: from here, not from `UsageMeter()` below — the meter is only
    # created once the response headers are already back, which would miss
    # connection time and time-to-first-byte entirely.
    call_started = time.monotonic()
    resp = await client.send(req, stream=True)

    # US-33.1: tee the stream. The caller gets exactly the bytes the provider
    # sent — the meter only watches them go past.
    meter = metering.UsageMeter(provider["provider_type"])

    async def _relay():
        try:
            async for chunk in resp.aiter_raw():
                meter.feed(chunk)
                yield chunk
        finally:
            await resp.aclose()
            await client.aclose()
            # Measuring is the secondary duty and loses every conflict: the
            # response has already reached the caller by the time this runs, and
            # nothing in here can be allowed to raise into the relay.
            await _meter_call(
                settings, claims, provider, _model, meter, resp.status_code, call_started
            )

    # Safety net behind the identity request: a provider that compresses anyway
    # must at least be labeled as such, or the caller decodes garbage.
    out_headers = {
        "content-type": resp.headers.get("content-type", "application/json")
    }
    encoding = resp.headers.get("content-encoding")
    if encoding:
        out_headers["content-encoding"] = encoding
    return StreamingResponse(
        _relay(),
        status_code=resp.status_code,
        headers=out_headers,
    )
