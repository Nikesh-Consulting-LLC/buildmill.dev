"""US-1.51: POST /llm/generate-deploy-script."""

import uuid
from types import SimpleNamespace

from app import llm as llm_module

PROJECT_ID = str(uuid.uuid4())


def _config(monkeypatch, provider="anthropic", model="claude-sonnet-5"):
    """One default provider serving every function (US-3.17 shape)."""

    async def fake_postgrest_get(settings, token, path, params):
        if path == "llm_providers":
            return [
                {
                    "id": "p1",
                    "org_id": "org",
                    "name": provider.capitalize(),
                    "provider_type": provider,
                    "base_url": None,
                    "models": [model],
                    "is_default": True,
                    "default_model": model,
                    "vault_secret_id": "11111111-2222-3333-4444-555555555555",
                }
            ]
        if path == "llm_function_routes":
            return []
        raise AssertionError(f"unexpected path {path}")

    monkeypatch.setattr(llm_module, "postgrest_get", fake_postgrest_get)


def _completion_reply(text):
    async def fake_acompletion(**kwargs):
        fake_acompletion.captured = kwargs
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=text))]
        )

    return fake_acompletion


def _patch_deps(
    monkeypatch,
    *,
    project=None,
    guidelines="## Stack\n\nNode.",
    deployment_section=False,
):
    async def fake_get(settings, token, path, params):
        if path == "projects":
            if project is None:
                return []
            return [project]
        # US-43.4: the endpoint asks whether this project has a Deployment and
        # Release section, so it can tell the manager whether the draft was
        # grounded in one or came from the stack and convention.
        if path == "project_guidelines":
            assert params["section_key"] == "eq.deployment"
            return [{"id": "sec-1"}] if deployment_section else []
        raise AssertionError(f"unexpected path {path}")

    async def fake_rpc(settings, token, fn, args):
        assert fn == "assemble_project_guidelines"
        assert args == {"p_project": PROJECT_ID}
        return guidelines

    monkeypatch.setattr("app.routers.llm.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.llm.rpc", fake_rpc)


_SAMPLE_PROJECT = {
    "id": PROJECT_ID,
    "name": "Demo App",
    "description": "A sample app",
    "repo_full_name": "acme/demo",
    "default_branch": "main",
}

_PAYLOAD = {
    "project_id": PROJECT_ID,
    "name": "Production",
    "branch": "main",
    "target_folder": "/var/www/demo",
    "source_folder": "apps/web",
    "strategy": "releases",
    "keep_releases": 5,
    "run_timeout_minutes": 30,
    "health_check_url": "http://localhost:3000/health",
    "env_var_names": ["DATABASE_URL", "API_KEY"],
}


def test_generate_deploy_script_requires_auth(client):
    resp = client.post("/api/v1/llm/generate-deploy-script", json=_PAYLOAD)
    assert resp.status_code == 401


def test_generate_deploy_script_happy_path(client, make_token, monkeypatch):
    _patch_deps(monkeypatch, project=_SAMPLE_PROJECT)
    _config(monkeypatch)
    monkeypatch.setattr(llm_module, "read_vault_secret", lambda s, sid: "sk-test")
    fake = _completion_reply(
        "```sh\nnpm ci\nnpm run build\nsystemctl restart demo\n```"
    )
    monkeypatch.setattr(llm_module.litellm, "acompletion", fake)

    resp = client.post(
        "/api/v1/llm/generate-deploy-script",
        json=_PAYLOAD,
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["model"] == "claude-sonnet-5"
    assert "npm ci" in body["script"]
    assert "```" not in body["script"]

    prompt = fake.captured["messages"][0]["content"]
    assert "Demo App" in prompt
    assert "acme/demo" in prompt

    # US-43.4: this project has no Deployment and Release section, so the
    # draft came from its stack and convention. The dialog says so rather
    # than presenting a plausible script as if it described the real deploy.
    assert body["grounded_in_deployment_section"] is False
    assert "## Stack" in prompt
    assert "/var/www/demo" in prompt
    assert "DATABASE_URL" in prompt
    assert "API_KEY" in prompt
    assert "SF_RELEASE_PATH" in prompt
    assert "SF_TARGET" in prompt
    # secrets / host credentials must never appear
    assert "sk-test" not in prompt
    assert "password" not in prompt.lower()


def test_generate_deploy_script_project_not_found_is_404(
    client, make_token, monkeypatch
):
    _patch_deps(monkeypatch, project=None)
    resp = client.post(
        "/api/v1/llm/generate-deploy-script",
        json=_PAYLOAD,
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 404


def test_generate_deploy_script_no_settings_is_409(client, make_token, monkeypatch):
    _patch_deps(monkeypatch, project=_SAMPLE_PROJECT)

    async def no_providers(settings, token, path, params):
        return []

    monkeypatch.setattr(llm_module, "postgrest_get", no_providers)
    resp = client.post(
        "/api/v1/llm/generate-deploy-script",
        json=_PAYLOAD,
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 409
    assert "Settings" in resp.json()["detail"]


def test_generate_deploy_script_provider_error_is_502(
    client, make_token, monkeypatch
):
    _patch_deps(monkeypatch, project=_SAMPLE_PROJECT)
    _config(monkeypatch)
    monkeypatch.setattr(llm_module, "read_vault_secret", lambda s, sid: "sk-test")

    async def boom(**kwargs):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(llm_module.litellm, "acompletion", boom)
    resp = client.post(
        "/api/v1/llm/generate-deploy-script",
        json=_PAYLOAD,
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 502
    assert "rate limited" in resp.json()["detail"]


def test_generate_deploy_script_strips_value_shaped_env_names(
    client, make_token, monkeypatch
):
    _patch_deps(monkeypatch, project=_SAMPLE_PROJECT)
    _config(monkeypatch)
    monkeypatch.setattr(llm_module, "read_vault_secret", lambda s, sid: "sk-test")
    fake = _completion_reply("echo ok")
    monkeypatch.setattr(llm_module.litellm, "acompletion", fake)

    payload = {
        **_PAYLOAD,
        "env_var_names": ["SAFE_NAME", "LEAK=secret-value", "OTHER"],
    }
    resp = client.post(
        "/api/v1/llm/generate-deploy-script",
        json=payload,
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 200
    prompt = fake.captured["messages"][0]["content"]
    assert "SAFE_NAME" in prompt
    assert "OTHER" in prompt
    assert "secret-value" not in prompt
    assert "LEAK=" not in prompt


def test_deploy_script_reports_grounding_when_the_section_exists(
    client, make_token, monkeypatch
):
    """US-43.4: the whole point of the Deployment and Release section is that
    this generator finally has something true to work from. The manager
    reading the output has to be able to tell which of the two they got."""
    _patch_deps(monkeypatch, project=_SAMPLE_PROJECT, deployment_section=True)
    _config(monkeypatch)
    monkeypatch.setattr(llm_module, "read_vault_secret", lambda s, sid: "sk-test")
    monkeypatch.setattr(
        llm_module.litellm, "acompletion", _completion_reply("npm ci")
    )

    resp = client.post(
        "/api/v1/llm/generate-deploy-script",
        json=_PAYLOAD,
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 200
    assert resp.json()["grounded_in_deployment_section"] is True
