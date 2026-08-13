"""US-34.1–34.4: the catalog, the surface, the proxy, and the record.

The load-bearing invariant across all four: an agent machine holds exactly ONE
kind of secret. A Supabase service key in an `mcp.json` on an agent box would turn
a compromised machine from "N revocable tokens" into "the org's credentials". So
the tests that matter most here are the ones asserting no catalog credential
reaches an agent, a log, or an audit row — and they are assertions, not
assumptions.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import mcp_tools


# ------------------------------------------------------- US-34.1: the catalog


def _http(**over):
    entry = {
        "name": "Sentry",
        "transport": "http",
        "endpoint": "https://mcp.sentry.dev/mcp",
        "needs_credential": True,
        "credential_header": "Authorization",
        "declared_tools": ["find_errors", "get_issue"],
    }
    entry.update(over)
    return entry


def test_a_well_formed_http_entry_is_accepted():
    out = mcp_tools.clean_entry(_http())
    assert out["slug"] == "sentry"
    assert out["transport"] == "http"
    assert out["command"] is None
    assert out["declared_tools"] == ["find_errors", "get_issue"]


def test_a_credential_free_stdio_entry_is_first_class():
    """Playwright and a docs lookup close the largest capability gap with no
    secret at all; they must not be gated behind the credentialed path."""
    out = mcp_tools.clean_entry(
        {
            "name": "Playwright",
            "transport": "stdio",
            "command": "npx @playwright/mcp@latest",
            "needs_credential": False,
        }
    )
    assert out["needs_credential"] is False
    assert out["command"] == "npx @playwright/mcp@latest"
    assert out["endpoint"] is None


def test_a_slug_is_derived_when_absent_and_is_config_safe():
    out = mcp_tools.clean_entry(_http(name="My Team's Sentry!"))
    assert out["slug"] == "my-team-s-sentry"
    assert mcp_tools.SLUG_RE.match(out["slug"])


@pytest.mark.parametrize("slug", ["-bad", "bad-", "has space", "x" * 45, "sl/ash"])
def test_a_structurally_unusable_slug_is_refused_not_sanitised(slug):
    """It becomes the server's key in the agent's MCP config; a slug that changed
    shape silently under the manager is a server they cannot find again."""
    with pytest.raises(mcp_tools.CatalogInvalid) as e:
        mcp_tools.clean_entry(_http(slug=slug))
    assert "usable id" in str(e.value)


def test_case_and_padding_are_normalised_because_a_slug_is_lowercase_by_definition():
    """Normalising the case of an identifier is not the same as changing it —
    `Sentry` and `sentry` were never two different servers."""
    assert mcp_tools.clean_entry(_http(slug="  Sentry "))["slug"] == "sentry"


def test_a_one_character_name_is_a_legitimate_server():
    out = mcp_tools.clean_entry(_http(name="X", slug=None))
    assert out["slug"] == "x"


@pytest.mark.parametrize("transport", ["", "grpc", "websocket", "ssh"])
def test_an_unknown_transport_is_refused(transport):
    with pytest.raises(mcp_tools.CatalogInvalid) as e:
        mcp_tools.clean_entry(_http(transport=transport))
    assert "http" in str(e.value) and "stdio" in str(e.value)


def test_a_transport_is_matched_case_insensitively():
    assert mcp_tools.clean_entry(_http(transport="HTTP "))["transport"] == "http"


def test_an_http_entry_needs_a_reachable_looking_endpoint():
    with pytest.raises(mcp_tools.CatalogInvalid):
        mcp_tools.clean_entry(_http(endpoint=""))
    with pytest.raises(mcp_tools.CatalogInvalid) as e:
        mcp_tools.clean_entry(_http(endpoint="mcp.sentry.dev"))
    assert "http(s)" in str(e.value)


def test_a_stdio_entry_needs_a_command():
    with pytest.raises(mcp_tools.CatalogInvalid):
        mcp_tools.clean_entry(
            {"name": "X", "transport": "stdio", "command": "   "}
        )


def test_a_credentialed_http_entry_must_say_how_to_present_it():
    """Without it the proxy would resolve a secret it cannot use — failing at the
    first tool call rather than at registration."""
    with pytest.raises(mcp_tools.CatalogInvalid) as e:
        mcp_tools.clean_entry(_http(credential_header=""))
    assert "header" in str(e.value)


def test_the_wrong_target_for_the_transport_is_dropped_not_kept():
    out = mcp_tools.clean_entry(
        {
            "name": "X",
            "transport": "stdio",
            "command": "run me",
            "endpoint": "https://leftover.example",
        }
    )
    assert out["endpoint"] is None


def test_a_name_is_required_and_bounded():
    with pytest.raises(mcp_tools.CatalogInvalid):
        mcp_tools.clean_entry(_http(name="  "))
    with pytest.raises(mcp_tools.CatalogInvalid):
        mcp_tools.clean_entry(_http(name="x" * 61))


def test_the_credential_rpc_mirrors_the_llm_one_including_its_refusals():
    """No second pattern for the same problem — a second pattern is a second
    thing to get wrong."""
    sql = (
        Path(__file__).resolve().parents[3]
        / "infra"
        / "supabase"
        / "migrations"
        / "162_mcp_server_catalog.sql"
    ).read_text(encoding="utf-8")
    assert "security definer" in sql
    assert "vault.create_secret" in sql and "vault.update_secret" in sql
    assert "key_last4 = right(p_key, 4)" in sql
    # us-31.4's autofill guards, for the same reason they exist there
    assert "contains whitespace" in sql
    assert "email address" in sql
    # existence and membership answer identically
    assert "not authorized" in sql
    # and no client write policy exists on the table
    assert "for select" in sql
    assert "for all" not in sql
    assert "for insert" not in sql


# ------------------------------------------------- US-34.3: the surface


CATALOG = {
    "s1": {
        "id": "s1", "slug": "sentry", "name": "Sentry", "transport": "http",
        "declared_tools": ["find_errors"], "needs_credential": True,
        "enabled": True, "last_check_ok": True,
    },
    "s2": {
        "id": "s2", "slug": "playwright", "name": "Playwright",
        "transport": "stdio", "declared_tools": ["browse"],
        "needs_credential": False, "enabled": True, "last_check_ok": None,
    },
    "s3": {
        "id": "s3", "slug": "off", "name": "Disabled one", "transport": "http",
        "declared_tools": [], "needs_credential": False, "enabled": False,
        "last_check_ok": True,
    },
    "s4": {
        "id": "s4", "slug": "broken", "name": "Broken one", "transport": "http",
        "declared_tools": [], "needs_credential": True, "enabled": True,
        "last_check_ok": False, "last_check_error": "the server answered 401",
    },
}


def test_no_grants_means_the_factory_server_and_nothing_else():
    """Default deny — the state us-31.9 ships."""
    surface = mcp_tools.compose_surface(grants=[], catalog=CATALOG, withheld=[])
    assert surface["granted"] == []
    assert surface["factory"] is True


def test_registering_a_server_grants_it_to_nobody():
    """An admin adding a server must not accidentally re-tool every run in the
    org — the same fail-closed principle us-31.3 applies to project grants."""
    surface = mcp_tools.compose_surface(grants=None, catalog=CATALOG, withheld=[])
    assert surface["granted"] == []


def test_a_granted_server_arrives_with_its_declared_tools():
    surface = mcp_tools.compose_surface(grants=["s1"], catalog=CATALOG, withheld=[])
    assert len(surface["granted"]) == 1
    got = surface["granted"][0]
    assert got["slug"] == "sentry"
    assert got["tools"] == ["find_errors"]
    assert got["proxied"] is True


def test_a_credential_free_stdio_server_is_local_and_says_it_is_unaudited():
    """It never passes through the proxy, so it cannot be recorded there. Said,
    not implied (us-34.4)."""
    surface = mcp_tools.compose_surface(grants=["s2"], catalog=CATALOG, withheld=[])
    assert surface["granted"][0]["proxied"] is False
    assert surface["unaudited"] == ["s2"]
    assert surface["audited"] == []


def test_a_project_may_withhold_what_the_preset_granted():
    """Presets are shared across projects; a database tool right for one is wrong
    for another. The effective surface is the intersection."""
    surface = mcp_tools.compose_surface(
        grants=["s1", "s2"], catalog=CATALOG, withheld=["s1"]
    )
    assert [g["slug"] for g in surface["granted"]] == ["playwright"]
    assert surface["withheld"][0]["name"] == "Sentry"
    assert "project withholds" in surface["withheld"][0]["why"]


def test_a_removed_catalog_entry_is_reported_not_dropped():
    surface = mcp_tools.compose_surface(
        grants=["gone"], catalog=CATALOG, withheld=[]
    )
    assert surface["granted"] == []
    assert surface["unavailable"][0]["why"] == "no longer in the catalog"


def test_a_disabled_or_failing_server_is_named_with_its_reason():
    surface = mcp_tools.compose_surface(
        grants=["s3", "s4"], catalog=CATALOG, withheld=[]
    )
    whys = {u["name"]: u["why"] for u in surface["unavailable"]}
    assert whys["Disabled one"] == "disabled in the catalog"
    assert whys["Broken one"] == "the server answered 401"


def test_the_notes_tell_the_agent_what_is_missing_and_why():
    """A run starts with the tool absent and the agent TOLD, rather than silently
    receiving a smaller toolset."""
    surface = mcp_tools.compose_surface(
        grants=["s1", "s4", "gone"], catalog=CATALOG, withheld=["s1"]
    )
    notes = mcp_tools.surface_notes(surface)
    assert any("Sentry" in n and "project" in n for n in notes)
    assert any("401" in n for n in notes)
    assert len(notes) == 3


def test_a_duplicate_grant_is_not_a_duplicate_server():
    surface = mcp_tools.compose_surface(
        grants=["s2", "s2"], catalog=CATALOG, withheld=[]
    )
    # compose does not dedupe by itself; the preset validator does — assert the
    # validator is where that belongs so the two cannot both half-do it.
    from app import presets

    assert presets.clean_tool_grants(["s2", "s2"]) == ["s2"]
    assert len(surface["granted"]) == 2  # honest about what it was handed


# ------------------------------------------------- US-34.4: what is recorded


def test_an_argument_named_like_a_secret_is_removed():
    out = mcp_tools.redact_arguments(
        {"query": "select 1", "api_key": "abcd1234", "authToken": "xyz"}
    )
    assert out["query"] == "select 1"
    assert out["api_key"] == mcp_tools.REDACTED
    assert out["authToken"] == mcp_tools.REDACTED


@pytest.mark.parametrize(
    "value",
    [
        "sk-abcdefghijklmnop",
        "sk_live_abcdefghij",
        "sfw_abcdefghijklmnop",
        "sfg_abcdefghijklmnop",
        "sfm_abcdefghijklmnop",
        "ghp_abcdefghijklmnop",
        "xoxb-abcdefghijklmnop",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig",
    ],
)
def test_a_value_that_looks_like_a_credential_is_removed_whatever_it_is_called(value):
    """Removed, not truncated: a truncated secret is still a secret."""
    out = mcp_tools.redact_arguments({"harmless_name": value})
    assert out["harmless_name"] == mcp_tools.REDACTED
    assert value[:8] not in json.dumps(out)


def test_this_factorys_own_key_prefixes_are_covered():
    """The scoped MCP key is `sfm_`; it must never appear in the audit of the very
    calls it authenticates."""
    for prefix in ("sfw_", "sfg_", "sfm_"):
        assert prefix in mcp_tools.SECRET_VALUE_RE.pattern


def test_long_free_text_is_summarised_not_stored():
    """A prompt or a file body is project data, and this table is readable by any
    org member."""
    out = mcp_tools.redact_arguments({"body": "x" * 5000})
    assert out["body"] == "[5000 chars]"


def test_nesting_is_bounded_and_wide_objects_are_summarised():
    deep = {"a": {"b": {"c": {"d": {"e": {"f": "too far"}}}}}}
    assert mcp_tools.REDACTED in json.dumps(mcp_tools.redact_arguments(deep))
    wide = {f"k{i}": i for i in range(50)}
    out = mcp_tools.redact_arguments(wide)
    assert len(out) <= mcp_tools.MAX_ARG_KEYS + 1
    assert "more argument(s)" in out["…"]


def test_scalars_and_none_survive_intact():
    out = mcp_tools.redact_arguments({"n": 5, "f": 1.5, "b": True, "z": None})
    assert out == {"n": 5, "f": 1.5, "b": True, "z": None}


def test_a_secret_nested_in_a_list_is_still_removed():
    out = mcp_tools.redact_arguments({"items": [{"token": "abc"}, "sk-abcdefghij"]})
    assert out["items"][0]["token"] == mcp_tools.REDACTED
    assert out["items"][1] == mcp_tools.REDACTED


def test_redaction_never_raises_on_anything():
    class Weird:
        pass

    for value in (Weird(), object(), b"bytes", {1: Weird()}):
        mcp_tools.redact_arguments(value)  # must not raise


# --------------------------------------- the leak assertions the stories demand


def test_no_response_shape_in_the_catalog_router_can_carry_the_secret():
    src = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "routers"
        / "mcp_catalog.py"
    ).read_text(encoding="utf-8")
    # the public column list is what every read goes through
    dbsrc = (Path(__file__).resolve().parents[1] / "app" / "db.py").read_text(
        encoding="utf-8"
    )
    public = dbsrc.split("_MCP_PUBLIC_COLUMNS = (", 1)[1].split(")", 1)[0]
    assert "vault_secret_id" not in public
    assert "key_last4" in public  # at most a last four
    # and the shaper strips anything a future column might add
    assert '"vault_secret_id", "credential", "secret"' in src


def test_only_the_proxy_reads_a_catalog_credential():
    """One privileged read, in one place, whose result never enters a response, a
    log, a trace or an audit row."""
    dbsrc = (Path(__file__).resolve().parents[1] / "app" / "db.py").read_text(
        encoding="utf-8"
    )
    assert dbsrc.count("def read_mcp_server_credential") == 1
    app_dir = Path(__file__).resolve().parents[1] / "app"
    callers = [
        p.name
        for p in app_dir.rglob("*.py")
        if "read_mcp_server_credential" in p.read_text(encoding="utf-8")
        and p.name != "db.py"
    ]
    assert callers == ["mcp_catalog.py"], callers


def test_the_api_never_receives_a_credential_at_all():
    """The smallest surface available: the browser writes it straight to Vault
    through the membership-gated RPC, so it never enters an API request body and
    therefore cannot appear in an API log or a traceback.

    A live check against the dev database found the alternative was not even
    possible — the RPC's `is_org_member` guard refuses the API's own connection,
    which has no `auth.uid()`. Following the established path was both safer and
    the only thing that works."""
    src = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "routers"
        / "mcp_catalog.py"
    ).read_text(encoding="utf-8")
    body = src.split("class ServerBody", 1)[1].split("async def", 1)[0]
    assert "credential: str" not in body
    assert "credential_header" in body  # the NAME of the header is not a secret
    dbsrc = (Path(__file__).resolve().parents[1] / "app" / "db.py").read_text(
        encoding="utf-8"
    )
    assert "set_mcp_server_key_as_org" not in dbsrc


def test_the_proxy_never_logs_or_audits_the_credential():
    src = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "routers"
        / "mcp_catalog.py"
    ).read_text(encoding="utf-8")
    proxy = src.split("async def proxy_mcp", 1)[1]
    # The credential is read into a local, used to build ONE header, and never
    # referenced again. Counting words proves nothing, so assert the shape:
    # every line mentioning it is either the read, the guard, or the header.
    lines = [
        ln.strip()
        for ln in proxy.splitlines()
        if "credential" in ln and not ln.strip().startswith("#")
    ]
    for line in lines:
        assert (
            "read_mcp_server_credential" in line
            or "needs_credential" in line
            or "credential_header" in line
            or line.startswith("if not credential")
            or line.startswith("f\"Bearer {credential}\"")
            or "no credential configured" in line
        ), line
    # and the audit dict is built from a fixed set of fields, none of them it
    audit = proxy.split("_audit(", 1)[1].split("    )", 1)[0]
    assert "credential" not in audit
