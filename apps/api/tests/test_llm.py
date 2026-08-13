"""US-1.16 /llm/elaborate-test + US-3.17 multi-provider routing & failover."""

from types import SimpleNamespace

from app import llm as llm_module

VAULT_A = "11111111-2222-3333-4444-555555555555"
VAULT_B = "66666666-7777-8888-9999-000000000000"


def _provider(
    id="p-default",
    name="Anthropic",
    provider_type="anthropic",
    models=("claude-sonnet-5",),
    is_default=True,
    default_model=None,
    base_url=None,
    vault_secret_id=VAULT_A,
):
    return {
        "id": id,
        "org_id": "org",
        "name": name,
        "provider_type": provider_type,
        "base_url": base_url,
        "models": list(models),
        "is_default": is_default,
        "default_model": default_model
        or (list(models)[0] if is_default else None),
        "vault_secret_id": vault_secret_id,
    }


def _org_llm(monkeypatch, providers, routes=()):
    """Serve llm_providers / llm_function_routes through the resolver's reads."""

    async def fake_postgrest_get(settings, token, path, params):
        if path == "llm_providers":
            return [dict(p) for p in providers]
        if path == "llm_function_routes":
            return [dict(r) for r in routes]
        raise AssertionError(f"unexpected path {path}")

    monkeypatch.setattr(llm_module, "postgrest_get", fake_postgrest_get)


def _vault(monkeypatch, secrets):
    monkeypatch.setattr(
        llm_module, "read_vault_secret", lambda s, sid: secrets.get(sid)
    )


def _completion_reply(text):
    async def fake_acompletion(**kwargs):
        fake_acompletion.captured = kwargs
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=text))]
        )

    return fake_acompletion


def _completion_fail_then_reply(text, fail_times=1):
    calls = []

    async def fake_acompletion(**kwargs):
        calls.append(kwargs)
        if len(calls) <= fail_times:
            raise RuntimeError("rate limited")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=text))]
        )

    fake_acompletion.calls = calls
    return fake_acompletion


_ELABORATE_JSON = (
    '{"title": "Check footer", "steps": "1. Open app", "expected_result": "Version shown"}'
)


def _elaborate(client, make_token, description="footer shows the version"):
    return client.post(
        "/api/v1/llm/elaborate-test",
        json={"description": description},
        headers={"Authorization": f"Bearer {make_token()}"},
    )


def test_elaborate_requires_auth(client):
    resp = client.post("/api/v1/llm/elaborate-test", json={"description": "check x"})
    assert resp.status_code == 401


def test_elaborate_uses_default_when_unrouted(client, make_token, monkeypatch):
    _org_llm(monkeypatch, [_provider()])
    _vault(monkeypatch, {VAULT_A: "sk-test"})
    fake = _completion_reply(_ELABORATE_JSON)
    monkeypatch.setattr(llm_module.litellm, "acompletion", fake)

    resp = _elaborate(client, make_token)
    assert resp.status_code == 200
    assert resp.json() == {
        "title": "Check footer",
        "steps": "1. Open app",
        "expected_result": "Version shown",
    }
    # default provider's default_model, litellm prefix applied, Vault key used
    assert fake.captured["model"] == "anthropic/claude-sonnet-5"
    assert fake.captured["api_key"] == "sk-test"


def test_elaborate_strips_code_fences(client, make_token, monkeypatch):
    _org_llm(
        monkeypatch,
        [_provider(name="Groq", provider_type="groq", models=("llama-3.3-70b",))],
    )
    _vault(monkeypatch, {VAULT_A: "gsk-test"})
    fake = _completion_reply(
        '```json\n{"title": "T", "steps": "1. Do", "expected_result": "Ok"}\n```'
    )
    monkeypatch.setattr(llm_module.litellm, "acompletion", fake)

    resp = _elaborate(client, make_token)
    assert resp.status_code == 200
    assert resp.json()["steps"] == "1. Do"
    assert fake.captured["model"] == "groq/llama-3.3-70b"


def test_route_overrides_default(client, make_token, monkeypatch):
    _org_llm(
        monkeypatch,
        [
            _provider(),
            _provider(
                id="p-fast",
                name="Groq",
                provider_type="groq",
                models=("llama-3.3-70b", "openai/gpt-oss-120b"),
                is_default=False,
                vault_secret_id=VAULT_B,
            ),
        ],
        routes=[
            {
                "function_key": "test_case_elaborate",
                "provider_id": "p-fast",
                "model": "openai/gpt-oss-120b",
            }
        ],
    )
    _vault(monkeypatch, {VAULT_A: "sk-test", VAULT_B: "gsk-test"})
    fake = _completion_reply(_ELABORATE_JSON)
    monkeypatch.setattr(llm_module.litellm, "acompletion", fake)

    resp = _elaborate(client, make_token)
    assert resp.status_code == 200
    assert fake.captured["model"] == "groq/openai/gpt-oss-120b"
    assert fake.captured["api_key"] == "gsk-test"


def test_stale_route_model_falls_back_to_default(client, make_token, monkeypatch):
    _org_llm(
        monkeypatch,
        [
            _provider(),
            _provider(
                id="p-fast",
                name="Groq",
                provider_type="groq",
                models=("llama-3.3-70b",),
                is_default=False,
                vault_secret_id=VAULT_B,
            ),
        ],
        # the routed model was removed from the provider's list
        routes=[
            {
                "function_key": "test_case_elaborate",
                "provider_id": "p-fast",
                "model": "removed-model",
            }
        ],
    )
    _vault(monkeypatch, {VAULT_A: "sk-test", VAULT_B: "gsk-test"})
    fake = _completion_reply(_ELABORATE_JSON)
    monkeypatch.setattr(llm_module.litellm, "acompletion", fake)

    resp = _elaborate(client, make_token)
    assert resp.status_code == 200
    assert fake.captured["model"] == "anthropic/claude-sonnet-5"


def test_routed_failure_retries_default_once(client, make_token, monkeypatch):
    _org_llm(
        monkeypatch,
        [
            _provider(),
            _provider(
                id="p-fast",
                name="Groq",
                provider_type="groq",
                models=("llama-3.3-70b",),
                is_default=False,
                vault_secret_id=VAULT_B,
            ),
        ],
        routes=[
            {
                "function_key": "test_case_elaborate",
                "provider_id": "p-fast",
                "model": "llama-3.3-70b",
            }
        ],
    )
    _vault(monkeypatch, {VAULT_A: "sk-test", VAULT_B: "gsk-test"})
    fake = _completion_fail_then_reply(_ELABORATE_JSON, fail_times=1)
    monkeypatch.setattr(llm_module.litellm, "acompletion", fake)

    resp = _elaborate(client, make_token)
    assert resp.status_code == 200
    assert len(fake.calls) == 2
    assert fake.calls[0]["model"] == "groq/llama-3.3-70b"
    assert fake.calls[1]["model"] == "anthropic/claude-sonnet-5"


def test_routed_and_default_failures_are_502_with_both_errors(
    client, make_token, monkeypatch
):
    _org_llm(
        monkeypatch,
        [
            _provider(),
            _provider(
                id="p-fast",
                name="Groq",
                provider_type="groq",
                models=("llama-3.3-70b",),
                is_default=False,
                vault_secret_id=VAULT_B,
            ),
        ],
        routes=[
            {
                "function_key": "test_case_elaborate",
                "provider_id": "p-fast",
                "model": "llama-3.3-70b",
            }
        ],
    )
    _vault(monkeypatch, {VAULT_A: "sk-test", VAULT_B: "gsk-test"})
    fake = _completion_fail_then_reply("never", fail_times=2)
    monkeypatch.setattr(llm_module.litellm, "acompletion", fake)

    resp = _elaborate(client, make_token)
    assert resp.status_code == 502
    assert len(fake.calls) == 2
    detail = resp.json()["detail"]
    assert "Groq" in detail
    assert "Anthropic" in detail
    assert "rate limited" in detail


def test_default_is_not_retried_when_it_is_the_routed_target(
    client, make_token, monkeypatch
):
    _org_llm(
        monkeypatch,
        [_provider()],
        routes=[
            {
                "function_key": "test_case_elaborate",
                "provider_id": "p-default",
                "model": "claude-sonnet-5",
            }
        ],
    )
    _vault(monkeypatch, {VAULT_A: "sk-test"})
    fake = _completion_fail_then_reply("never", fail_times=99)
    monkeypatch.setattr(llm_module.litellm, "acompletion", fake)

    resp = _elaborate(client, make_token)
    assert resp.status_code == 502
    assert len(fake.calls) == 1


def test_routed_missing_key_falls_back_to_default(client, make_token, monkeypatch):
    _org_llm(
        monkeypatch,
        [
            _provider(),
            _provider(
                id="p-fast",
                name="Groq",
                provider_type="groq",
                models=("llama-3.3-70b",),
                is_default=False,
                vault_secret_id=None,
            ),
        ],
        routes=[
            {
                "function_key": "test_case_elaborate",
                "provider_id": "p-fast",
                "model": "llama-3.3-70b",
            }
        ],
    )
    _vault(monkeypatch, {VAULT_A: "sk-test"})
    fake = _completion_reply(_ELABORATE_JSON)
    monkeypatch.setattr(llm_module.litellm, "acompletion", fake)

    resp = _elaborate(client, make_token)
    assert resp.status_code == 200
    assert fake.captured["model"] == "anthropic/claude-sonnet-5"


def test_no_providers_is_409(client, make_token, monkeypatch):
    _org_llm(monkeypatch, [])
    resp = _elaborate(client, make_token)
    assert resp.status_code == 409
    assert "Settings" in resp.json()["detail"]


def test_default_without_key_is_409(client, make_token, monkeypatch):
    _org_llm(monkeypatch, [_provider(vault_secret_id=None)])
    _vault(monkeypatch, {})
    resp = _elaborate(client, make_token)
    assert resp.status_code == 409
    assert "Settings" in resp.json()["detail"]


def test_ollama_default_needs_no_key(client, make_token, monkeypatch):
    _org_llm(
        monkeypatch,
        [
            _provider(
                name="Ollama",
                provider_type="ollama",
                models=("qwen3",),
                base_url="http://localhost:11434",
                vault_secret_id=None,
            )
        ],
    )
    _vault(monkeypatch, {})
    fake = _completion_reply('{"title": "T", "steps": "1. Do", "expected_result": "Ok"}')
    monkeypatch.setattr(llm_module.litellm, "acompletion", fake)

    resp = _elaborate(client, make_token)
    assert resp.status_code == 200
    assert fake.captured["model"] == "ollama/qwen3"
    assert fake.captured["api_base"] == "http://localhost:11434"
    assert "api_key" not in fake.captured


# ---------------------------------------------------------------- registry


def test_functions_requires_auth(client):
    resp = client.get("/api/v1/llm/functions")
    assert resp.status_code == 401


def test_functions_lists_registry_in_order(client, make_token):
    resp = client.get(
        "/api/v1/llm/functions",
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert [f["key"] for f in body] == [
        "prd_draft",
        # us-18.1 added content_tldr and never updated this list, so the
        # assertion had been failing since Phase 18; corrected in us-20.2.
        "content_tldr",
        # us-25.3: the whole-work-item summary, distinct from content_tldr's
        # single-block digest.
        "work_item_tldr",
        "story_breakdown",
        "test_case_elaborate",
        "learnings_merge",
        "deploy_script_generate",
        "story_complexity_score",
        "plan_complexity_score",
    ]
    for f in body:
        assert f["label"]
        assert f["description"]
