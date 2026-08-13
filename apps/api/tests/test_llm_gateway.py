"""US-10.3: runner LLM gateway — module env, auth mapping, scoped-key gate."""

from app.routers.llm_gateway import _auth_headers, module_env


def test_module_env_anthropic():
    env = module_env("anthropic", "https://f/api/v1/llm-gateway/", "sfg_x", "claude-5")
    assert env["ANTHROPIC_BASE_URL"] == "https://f/api/v1/llm-gateway"
    assert env["ANTHROPIC_API_KEY"] == "sfg_x"
    assert env["ANTHROPIC_MODEL"] == "claude-5"


def test_module_env_xai_and_openai():
    # Measured live against the real grok CLI's current generation (1.1.7,
    # US-10.5 follow-up): GROK_API_KEY / GROK_BASE_URL / GROK_MODEL, with
    # /v1 appended to the base the same way OpenCode's does.
    xai = module_env("xai", "https://f/gw", "k", "grok-4.5")
    assert xai["GROK_API_KEY"] == "k"
    assert xai["GROK_BASE_URL"] == "https://f/gw/v1"
    assert xai["GROK_MODEL"] == "grok-4.5"
    oai = module_env("openai", "https://f/gw", "k", "gpt")
    assert oai["OPENAI_BASE_URL"] == "https://f/gw" and oai["OPENAI_API_KEY"] == "k"


def test_auth_headers_by_provider():
    assert _auth_headers("anthropic", "k") == {"x-api-key": "k"}
    assert _auth_headers("xai", "k") == {"Authorization": "Bearer k"}
    assert _auth_headers("openai", "k") == {"Authorization": "Bearer k"}


def test_proxy_rejects_invalid_scoped_key(client, monkeypatch):
    monkeypatch.setattr(
        "app.routers.llm_gateway.db.validate_gateway_key", lambda s, k: None
    )
    resp = client.post(
        "/api/v1/llm-gateway/v1/messages",
        headers={"x-api-key": "sfg_bad"},
        json={"model": "claude", "messages": []},
    )
    assert resp.status_code == 401


# ------------------- US-27.8: the model decides which provider answers


import pytest  # noqa: E402

from app.routers import llm_gateway  # noqa: E402
from app.routers.runner_socket import validate_model_provider_pairing  # noqa: E402


ANTHROPIC = {
    "id": "p-anthropic",
    "name": "Anthropic",
    "provider_type": "anthropic",
    "models": ["claude-sonnet-5", "claude-opus-5"],
    "is_default": False,
    "default_model": None,
    "vault_secret_id": "v1",
    "base_url": None,
}
GROQ = {
    "id": "p-groq",
    "name": "Groq",
    "provider_type": "groq",
    "models": ["openai/gpt-oss-120b"],
    "is_default": True,
    "default_model": "openai/gpt-oss-120b",
    "vault_secret_id": "v2",
    "base_url": None,
}


def _config(monkeypatch, providers, routes=None):
    monkeypatch.setattr(
        "app.routers.llm_gateway.db.get_org_llm_config",
        lambda s, org: (providers, routes or {}),
    )
    monkeypatch.setattr(
        "app.routers.llm_gateway.llm.read_vault_secret", lambda s, sid: "real-key"
    )


def test_the_routed_model_picks_its_own_provider(monkeypatch):
    """The 2026-07-26 configuration: Groq is the org default and the agent is
    routed to claude-sonnet-5. The request must reach Anthropic."""
    _config(monkeypatch, [GROQ, ANTHROPIC])
    provider, model, key, base = llm_gateway._resolve_provider(
        object(), "org-1", "runner_code", "claude-sonnet-5"
    )
    assert provider["provider_type"] == "anthropic"
    assert model == "claude-sonnet-5"
    assert key == "real-key"
    assert base == "https://api.anthropic.com"


def test_without_a_model_the_brain_still_routes_the_old_way(monkeypatch):
    """A fleet on Claude can still think on Groq — llm.infer and any key
    minted before US-27.8 keep resolving through the org default."""
    _config(monkeypatch, [GROQ, ANTHROPIC])
    provider, model, _key, _base = llm_gateway._resolve_provider(
        object(), "org-1", "runner_brain", None
    )
    assert provider["provider_type"] == "groq"
    assert model == "openai/gpt-oss-120b"


def test_a_model_no_provider_offers_is_named_as_such(monkeypatch):
    _config(monkeypatch, [GROQ, ANTHROPIC])
    with pytest.raises(llm_gateway.NoProviderForModel) as e:
        llm_gateway._resolve_provider(
            object(), "org-1", "runner_code", "claude-sonnet-9"
        )
    assert e.value.model == "claude-sonnet-9"
    assert "claude-sonnet-5" in e.value.offered


def test_provider_for_model_is_exact():
    assert llm_gateway.provider_for_model([ANTHROPIC], "claude-sonnet-5") is ANTHROPIC
    assert llm_gateway.provider_for_model([ANTHROPIC], "claude-sonnet") is None


# ------------------- US-60.1: Buildmill Agent resolves the platform's key


def test_resolve_platform_provider_uses_the_platform_key(monkeypatch):
    monkeypatch.setattr(
        "app.routers.llm_gateway.db.get_platform_llm_key",
        lambda s: {"model": "claude-sonnet-5", "key": "platform-key"},
    )
    provider, model, key, base = llm_gateway._resolve_platform_provider(
        object(), None
    )
    assert provider["provider_type"] == "anthropic"
    assert provider["name"] == "Buildmill Agent"
    assert provider["id"] is None
    assert model == "claude-sonnet-5"
    assert key == "platform-key"
    assert base == "https://api.anthropic.com"


def test_resolve_platform_provider_prefers_the_requested_model(monkeypatch):
    monkeypatch.setattr(
        "app.routers.llm_gateway.db.get_platform_llm_key",
        lambda s: {"model": "claude-sonnet-5", "key": "platform-key"},
    )
    _provider, model, _key, _base = llm_gateway._resolve_platform_provider(
        object(), "claude-opus-5"
    )
    assert model == "claude-opus-5"


def test_resolve_platform_provider_with_no_key_set_returns_no_key(monkeypatch):
    monkeypatch.setattr(
        "app.routers.llm_gateway.db.get_platform_llm_key", lambda s: None
    )
    _provider, _model, key, base = llm_gateway._resolve_platform_provider(
        object(), None
    )
    assert key is None
    assert base == "https://api.anthropic.com"  # still forwardable, just keyless


def test_proxy_resolves_the_platform_key_for_a_platform_billed_call(client, monkeypatch):
    """US-60.1: the request reaches the platform's key, not the org's own
    configured provider — `get_org_llm_config` must never even be called."""
    recorded, FakeResponse = _streamed(monkeypatch, [b'{"usage":{}}'])
    monkeypatch.setattr(
        llm_gateway.db,
        "validate_gateway_key",
        lambda s, k: {
            "org_id": "11111111-1111-1111-1111-111111111111",
            "worker_id": "22222222-2222-2222-2222-222222222222",
            "run_id": None,
            "project_id": None,
            "route": "runner_code",
            "model": "claude-sonnet-5",
            "platform_billed": True,
        },
    )
    monkeypatch.setattr(
        llm_gateway.db,
        "get_platform_llm_key",
        lambda s: {"model": "claude-sonnet-5", "key": "platform-key"},
    )

    def refuse_org_config(*a, **kw):
        raise AssertionError("must not resolve against the org's own providers")

    monkeypatch.setattr(llm_gateway.db, "get_org_llm_config", refuse_org_config)

    resp = client.post(
        "/api/v1/llm-gateway/v1/messages",
        headers={"x-api-key": "sfg_x"},
        json={"model": "claude-sonnet-5"},
    )
    assert resp.status_code == 200, resp.text
    assert recorded["headers"]["x-api-key"] == "platform-key"


def test_proxy_names_the_platform_key_gap_distinctly(monkeypatch, client):
    monkeypatch.setattr(
        llm_gateway.db,
        "validate_gateway_key",
        lambda s, k: {
            "org_id": "11111111-1111-1111-1111-111111111111",
            "worker_id": "22222222-2222-2222-2222-222222222222",
            "run_id": None,
            "project_id": None,
            "route": "runner_code",
            "model": "claude-sonnet-5",
            "platform_billed": True,
        },
    )
    monkeypatch.setattr(llm_gateway.db, "get_platform_llm_key", lambda s: None)
    resp = client.post(
        "/api/v1/llm-gateway/v1/messages",
        headers={"x-api-key": "sfg_x"},
        json={"model": "claude-sonnet-5"},
    )
    assert resp.status_code == 502
    assert "superadmin" in resp.json()["detail"]
    assert "Settings" not in resp.json()["detail"]
    assert llm_gateway.provider_for_model([ANTHROPIC], "") is None


def test_saving_a_module_pointed_at_the_wrong_providers_model_is_refused():
    problem = validate_model_provider_pairing(
        ["claude"], {"code": "openai/gpt-oss-120b"}, [GROQ, ANTHROPIC]
    )
    assert problem
    assert "claude" in problem and "openai/gpt-oss-120b" in problem
    assert "anthropic" in problem


def test_a_matching_pairing_is_accepted():
    assert (
        validate_model_provider_pairing(
            ["claude"], {"code": "claude-sonnet-5"}, [GROQ, ANTHROPIC]
        )
        is None
    )


def test_an_unknown_model_is_refused_at_save_time_too():
    problem = validate_model_provider_pairing(
        ["claude"], {"code": "claude-sonnet-9"}, [GROQ, ANTHROPIC]
    )
    assert problem and "no configured provider offers" in problem


def test_the_brain_route_is_not_second_guessed():
    """`brain` is not a CLI module's run kind — it goes through llm.infer,
    which routes server-side. Pairing rules must not reach it."""
    assert (
        validate_model_provider_pairing(
            ["claude"], {"brain": "openai/gpt-oss-120b"}, [GROQ, ANTHROPIC]
        )
        is None
    )


# ------------------- US-33.1: metering, and what it must never do


def _streamed(monkeypatch, chunks, provider=ANTHROPIC, status=200):
    """Drive the real proxy against a fake upstream and return what the caller
    received, plus whatever usage row the meter produced."""
    import asyncio

    recorded = {}

    class FakeResponse:
        status_code = status
        headers = {"content-type": "text/event-stream"}

        async def aiter_raw(self):
            for c in chunks:
                yield c

        async def aclose(self):
            pass

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        def build_request(self, *a, **kw):
            recorded["content"] = kw.get("content")
            recorded["headers"] = kw.get("headers")
            return object()

        async def send(self, req, stream=True):
            return FakeResponse()

        async def aclose(self):
            pass

    monkeypatch.setattr(llm_gateway.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(
        llm_gateway.db,
        "validate_gateway_key",
        lambda s, k: {
            "org_id": "11111111-1111-1111-1111-111111111111",
            "worker_id": "22222222-2222-2222-2222-222222222222",
            "run_id": "33333333-3333-3333-3333-333333333333",
            "project_id": "44444444-4444-4444-4444-444444444444",
            "route": "runner_code",
            "model": provider["models"][0],
        },
    )
    _config(monkeypatch, [provider])
    monkeypatch.setattr(llm_gateway.db, "model_prices", lambda s, org: {})
    monkeypatch.setattr(
        llm_gateway.db,
        "record_llm_usage",
        lambda s, usage: recorded.setdefault("usage", usage),
    )
    return recorded, FakeResponse


def test_a_call_records_its_own_latency(client, monkeypatch):
    """US-62.3: llm_usage never had a latency column before this — the row
    a call produces now always carries a non-negative measured duration."""
    chunks = [
        b'data: {"type":"message_start","message":{"usage":{"input_tokens":1,"output_tokens":0}}}\n\n',
        b'data: {"type":"message_delta","usage":{"output_tokens":2}}\n\n',
    ]
    recorded, _ = _streamed(monkeypatch, chunks)
    client.post(
        "/api/v1/llm-gateway/v1/messages",
        headers={"x-api-key": "sfg_ok"},
        content=b'{"model":"claude-sonnet-5","stream":true,"messages":[]}',
    )
    latency = recorded["usage"]["latency_ms"]
    assert isinstance(latency, int)
    assert latency >= 0


def test_metering_does_not_alter_the_bytes_the_caller_receives(client, monkeypatch):
    """The acceptance bar: the same stream, compared with metering on and with
    the meter stubbed out to a no-op. Byte for byte."""
    chunks = [
        b'data: {"type":"message_start","message":{"usage":{"input_tokens":9,"output_tokens":0}}}\n\n',
        b'data: {"type":"content_block_delta","delta":{"text":"hello"}}\n\n',
        b'data: {"type":"message_delta","usage":{"output_tokens":21}}\n\n',
    ]
    recorded, _ = _streamed(monkeypatch, chunks)
    metered = client.post(
        "/api/v1/llm-gateway/v1/messages",
        headers={"x-api-key": "sfg_ok"},
        content=b'{"model":"claude-sonnet-5","stream":true,"messages":[]}',
    )

    # Now with the meter neutered — the response must be identical.
    class NoopMeter:
        def __init__(self, *a, **kw):
            pass

        def feed(self, chunk):
            pass

        def finish(self):
            pass

        def as_row(self):
            return {"tokens_in": None, "tokens_out": None, "parsed": False, "parse_note": "off"}

    _streamed(monkeypatch, chunks)
    monkeypatch.setattr(llm_gateway.metering, "UsageMeter", NoopMeter)
    plain = client.post(
        "/api/v1/llm-gateway/v1/messages",
        headers={"x-api-key": "sfg_ok"},
        content=b'{"model":"claude-sonnet-5","stream":true,"messages":[]}',
    )

    assert metered.content == plain.content == b"".join(chunks)
    assert metered.status_code == plain.status_code == 200
    # ...and the usage row landed with the run it belongs to.
    usage = recorded["usage"]
    assert (usage["tokens_in"], usage["tokens_out"], usage["parsed"]) == (9, 21, True)
    assert usage["run_id"] == "33333333-3333-3333-3333-333333333333"
    assert usage["project_id"] == "44444444-4444-4444-4444-444444444444"
    assert usage["model"] == "claude-sonnet-5"
    assert usage["provider_name"] == "Anthropic"


def test_a_metering_write_failure_never_reaches_the_caller(client, monkeypatch):
    recorded, _ = _streamed(monkeypatch, [b'data: {"usage":{"input_tokens":1,"output_tokens":2}}\n\n'])

    def boom(settings, usage):
        raise RuntimeError("database is on fire")

    monkeypatch.setattr(llm_gateway.db, "record_llm_usage", boom)
    resp = client.post(
        "/api/v1/llm-gateway/v1/messages",
        headers={"x-api-key": "sfg_ok"},
        content=b"{}",
    )
    assert resp.status_code == 200
    assert resp.content == b'data: {"usage":{"input_tokens":1,"output_tokens":2}}\n\n'


def test_an_openai_shaped_stream_has_include_usage_injected(client, monkeypatch):
    recorded, _ = _streamed(monkeypatch, [b"data: [DONE]\n\n"], provider=GROQ)
    client.post(
        "/api/v1/llm-gateway/v1/chat/completions",
        headers={"x-api-key": "sfg_ok", "content-type": "application/json"},
        content=b'{"model":"openai/gpt-oss-120b","stream":true,"messages":[]}',
    )
    import json as _json

    forwarded = _json.loads(recorded["content"])
    assert forwarded["stream_options"] == {"include_usage": True}
    # A stale Content-Length would truncate the rewritten body upstream.
    assert "content-length" not in {k.lower() for k in recorded["headers"]}


def test_an_anthropic_request_body_is_forwarded_untouched(client, monkeypatch):
    recorded, _ = _streamed(monkeypatch, [b"data: [DONE]\n\n"])
    body = b'{"model":"claude-sonnet-5","stream":true,"messages":[]}'
    client.post(
        "/api/v1/llm-gateway/v1/messages",
        headers={"x-api-key": "sfg_ok", "content-type": "application/json"},
        content=body,
    )
    assert recorded["content"] == body


def test_an_unparsed_call_still_writes_a_row_with_null_tokens(client, monkeypatch):
    recorded, _ = _streamed(monkeypatch, [b"<html>bad gateway</html>"])
    client.post(
        "/api/v1/llm-gateway/v1/messages",
        headers={"x-api-key": "sfg_ok"},
        content=b"{}",
    )
    usage = recorded["usage"]
    assert usage["parsed"] is False
    assert usage["tokens_in"] is None and usage["tokens_out"] is None
    assert usage["cost_usd"] is None
    assert usage["parse_note"]


def test_the_upstream_is_asked_for_identity_encoding(client, monkeypatch):
    """The 2026-07-27 failure: nothing forwarded the client's accept-encoding,
    so httpx applied its own default and Anthropic answered in gzip. aiter_raw()
    relayed the compressed bytes, the response carried no content-encoding, and
    the CLI died on 'Failed to parse JSON' — while the meter read binary.
    Identity is the only encoding the caller and the meter can both always read."""
    recorded, _ = _streamed(monkeypatch, [b"data: [DONE]\n\n"])
    client.post(
        "/api/v1/llm-gateway/v1/messages",
        headers={"x-api-key": "sfg_ok", "accept-encoding": "gzip, br"},
        content=b"{}",
    )
    sent = {k.lower(): v for k, v in recorded["headers"].items()}
    assert sent.get("accept-encoding") == "identity"


def test_an_upstream_content_encoding_is_forwarded_not_dropped(client, monkeypatch):
    """The safety net behind identity: a provider that compresses anyway must at
    least be labeled as such, or the caller decodes garbage."""
    import gzip

    recorded, fake = _streamed(monkeypatch, [gzip.compress(b'{"ok":true}')])
    fake.headers = {
        "content-type": "application/json",
        "content-encoding": "gzip",
    }
    resp = client.post(
        "/api/v1/llm-gateway/v1/messages",
        headers={"x-api-key": "sfg_ok"},
        content=b"{}",
    )
    assert resp.headers.get("content-encoding") == "gzip"


def test_the_rate_in_force_is_recorded_on_the_row(client, monkeypatch):
    """A repriced model must not rewrite history, which needs the rate on the
    row rather than only in the price table."""
    recorded, _ = _streamed(
        monkeypatch,
        [b'data: {"usage":{"input_tokens":1000000,"output_tokens":1000000}}\n\n'],
    )
    monkeypatch.setattr(
        llm_gateway.db,
        "model_prices",
        lambda s, org: {"claude-sonnet-5": {"input_per_mtok": 3.0, "output_per_mtok": 15.0}},
    )
    client.post(
        "/api/v1/llm-gateway/v1/messages",
        headers={"x-api-key": "sfg_ok"},
        content=b"{}",
    )
    usage = recorded["usage"]
    assert usage["rate_in_per_mtok"] == 3.0
    assert usage["rate_out_per_mtok"] == 15.0
    assert usage["cost_usd"] == 18.0


def test_record_llm_usage_writes_latency_ms():
    """US-62.3: the insert itself carries the new column, not just the dict
    passed to it."""
    from app import db as real_db

    class FakeCursor:
        def fetchone(self):
            return None

    class FakeConn:
        def __init__(self):
            self.queries = []

        def execute(self, q, p=None):
            self.queries.append((" ".join(q.split()), p))
            return FakeCursor()

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *e):
            return False

    conn = FakeConn()
    import unittest.mock as mock

    with mock.patch.object(real_db, "_connect", lambda s: conn):
        real_db.record_llm_usage(
            object(),
            {
                "org_id": "11111111-1111-1111-1111-111111111111",
                "tokens_in": 1,
                "tokens_out": 2,
                "parsed": True,
                "latency_ms": 842,
            },
        )
    q, params = conn.queries[0]
    assert "latency_ms" in q
    assert params[-1] == 842
