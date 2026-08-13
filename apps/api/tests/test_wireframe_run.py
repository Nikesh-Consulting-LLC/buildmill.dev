"""US-48.2 — the wireframe run kind.

Covers the hand-back's shape tolerance (US-42.1's lesson), the no-UI verdict,
what gets written to the repository, and the preview the app renders. The SQL
half — dispatch refusals, the status guarantee, the hold exemption and the
capability gate — is exercised against the live database in
`test_wireframe_dispatch_sql.py`.
"""

import json
from pathlib import Path

import pytest

from app import wireframe_docs, wireframes
from app.factory_mcp import _as_declaration

DB_PY = (
    Path(__file__).resolve().parents[1] / "app" / "db.py"
).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# US-42.1: a hand-back is not refused over a field's shape
# ---------------------------------------------------------------------------


def test_declaration_passes_a_well_formed_object_through():
    doc, findings = _as_declaration({"screens": [{"name": "Queue"}]})
    assert doc["screens"][0]["name"] == "Queue"
    assert findings == []


def test_declaration_accepts_a_json_string():
    doc, findings = _as_declaration(json.dumps({"screens": [{"name": "Queue"}]}))
    assert doc["screens"][0]["name"] == "Queue"
    assert any("JSON string" in f for f in findings)


def test_declaration_accepts_a_bare_list_of_screens():
    doc, findings = _as_declaration([{"name": "Queue"}])
    assert doc["screens"][0]["name"] == "Queue"
    assert any("bare list" in f for f in findings)


def test_declaration_accepts_one_screen_as_an_object():
    doc, findings = _as_declaration({"screens": {"name": "Queue"}})
    assert doc["screens"] == [{"name": "Queue"}]
    assert any("wrapped it in a list" in f for f in findings)


def test_declaration_accepts_a_single_top_level_screen():
    doc, findings = _as_declaration(
        {"name": "Queue", "regions": [{"component": "card"}]}
    )
    assert doc["screens"][0]["name"] == "Queue"
    assert any("top-level screen" in f for f in findings)


def test_declaration_reports_unknown_components_without_refusing():
    doc, findings = _as_declaration(
        {"screens": [{"name": "x", "regions": [{"component": "carousel"}]}]}
    )
    assert doc["screens"]  # not discarded
    assert any("carousel" in f for f in findings)


def test_declaration_survives_junk():
    assert _as_declaration(None) == ({}, [])
    assert _as_declaration("")[0] == {}
    assert _as_declaration("not json")[0] == {}
    assert _as_declaration(42)[0] == {}
    doc, findings = _as_declaration({"screens": "nonsense"})
    assert doc["screens"] == []
    assert findings


# ---------------------------------------------------------------------------
# The repository write
# ---------------------------------------------------------------------------


def test_declaration_of_reads_text_or_dict():
    assert wireframe_docs.declaration_of({"content": '{"a": 1}'}) == {"a": 1}
    assert wireframe_docs.declaration_of({"content": {"a": 1}}) == {"a": 1}
    assert wireframe_docs.declaration_of({"content": "not json"}) == {}
    assert wireframe_docs.declaration_of({"content": None}) == {}
    # A JSON array is valid JSON but not a declaration.
    assert wireframe_docs.declaration_of({"content": "[1,2]"}) == {}


def test_kit_marker_records_the_code_hash_not_the_tokens():
    """The marker is what lets a fan-out skip re-reading a repo's stylesheet
    fifteen times. It must therefore be stable across projects with different
    tokens, and change when the kit's code changes."""
    marker = json.loads(wireframe_docs._marker(wireframes.kit_code_hash(), "a.css"))
    assert marker["kit"] == wireframes.kit_code_hash()
    assert marker["tokens_source"] == "a.css"
    # Two projects with different palettes hold the same kit version.
    assert wireframes.kit_code_hash() == wireframes.kit_code_hash()
    assert wireframes.kit_code_hash() != wireframes.kit_hash()


@pytest.mark.asyncio
async def test_write_wireframe_never_raises(monkeypatch):
    """The write is best-effort by contract: the agent has already drawn the
    screen and the artifact is already stored, so a GitHub problem must cost
    an error string and not the hand-back."""

    def boom(*_args, **_kwargs):
        raise RuntimeError("github is down")

    monkeypatch.setattr(wireframe_docs.db, "get_issue_for_wireframe", boom)
    result = await wireframe_docs.write_wireframe(None, "some-issue")
    assert "error" in result
    assert "github is down" in result["error"]


# ---------------------------------------------------------------------------
# The preview
# ---------------------------------------------------------------------------


DECLARATION = {
    "screens": [
        {
            "name": "Queue",
            "route": "/issues",
            "regions": [{"component": "card", "title": "Work"}],
        }
    ]
}


def test_preview_is_self_contained():
    """The panel's iframe is sandboxed WITHOUT allow-same-origin, so its
    origin is opaque and it can resolve nothing back to the app. A single
    linked stylesheet here renders an unstyled page in production and looks
    fine in any test that only checks for the declaration."""
    html = wireframes.build_preview("US-4.2", "A story", DECLARATION)
    assert "<link" not in html
    assert 'src="' not in html
    assert "http://" not in html
    assert "https://" not in html
    # The kit is really inlined, not referenced.
    assert "wf-card" in html  # from kit.css
    assert "BuildMillWireframe" in html  # from kit.js


def test_preview_uses_the_same_kit_as_the_repository():
    """A second renderer in the web app would be a second thing to keep in
    step, and the manager would be approving a picture the repo does not
    draw."""
    html = wireframes.build_preview("US-4.2", "A story", DECLARATION)
    assert wireframes.kit_asset("kit.css") in html
    # kit.js is escaped for inlining, so compare the escaped form.
    assert wireframes._inline_js(wireframes.kit_asset("kit.js")) in html


def test_inlined_js_cannot_close_its_own_script_block():
    source = 'const a = "</script>"; const b = 1 </ 2;'
    inlined = wireframes._inline_js(source)
    assert "</script>" not in inlined
    assert "<\\/script>" in inlined
    # Only the tag is touched — unrelated slashes survive.
    assert "1 </ 2" in inlined


def test_preview_takes_the_projects_tokens_when_given_them():
    html = wireframes.build_preview(
        "US-4.2", "A story", DECLARATION, ":root { --primary: rebeccapurple; }"
    )
    assert "rebeccapurple" in html
    # And falls back to the neutral default when not.
    assert "rebeccapurple" not in wireframes.build_preview(
        "US-4.2", "A story", DECLARATION
    )


# ---------------------------------------------------------------------------
# The no-UI verdict
# ---------------------------------------------------------------------------


def test_a_no_ui_verdict_declares_no_screens():
    verdict = {"no_ui_surface": True, "reason": "this is a migration"}
    assert wireframes.declared_screens(verdict) == []
    assert wireframes.summarize(verdict) == "no screens"


# ---------------------------------------------------------------------------
# US-48.4: what a plan and a code run are told
# ---------------------------------------------------------------------------


class _FakeArtifact(dict):
    pass


def _patch_wireframe(monkeypatch, content):
    from app import factory_mcp

    monkeypatch.setattr(
        factory_mcp.db,
        "get_current_wireframe",
        lambda settings, issue_id: (
            None if content is None else {"content": content, "version": 1}
        ),
    )
    monkeypatch.setattr(
        factory_mcp.db,
        "work_item_display_id",
        lambda *args, **kwargs: "US-4.2",
    )


def _section(run_kind="plan", stories=None):
    from app import factory_mcp

    return factory_mcp._wireframe_section(
        None,
        {"kind": run_kind, "issue_id": "i", "issue_type": "story"},
        stories,
    )


def test_a_plan_run_is_told_to_agree_with_the_screens(monkeypatch):
    _patch_wireframe(monkeypatch, json.dumps(DECLARATION))
    section = _section("plan")
    assert "## Wireframe" in section
    assert "Queue" in section and "/issues" in section
    assert "Surfaces touched" in section
    assert "under **Risks**" in section
    # It names the file rather than pasting it.
    assert "docs/wireframes/us-4.2.html" in section
    assert "<html" not in section


def test_a_code_run_is_told_the_file_is_already_on_disk(monkeypatch):
    _patch_wireframe(monkeypatch, json.dumps(DECLARATION))
    section = _section("code")
    assert "Build to it" in section
    assert "hand-back notes" in section


def test_the_section_costs_under_2kb_for_a_representative_screen(monkeypatch):
    """The AC's measured bound. Phase 38 measured a plan run at 1.4M input
    tokens already; a 40 KB page in every plan context would make that worse
    for no gain."""
    _patch_wireframe(monkeypatch, json.dumps(DECLARATION))
    assert len(_section("plan").encode()) < 2048


def test_a_story_with_no_wireframe_gets_no_section(monkeypatch):
    """A wireframe never blocks a plan: its absence is not a finding, and a
    story without one must plan exactly as it does today."""
    _patch_wireframe(monkeypatch, None)
    assert _section("plan") == ""
    assert _section("code") == ""


def test_a_no_ui_verdict_gets_no_section(monkeypatch):
    """There is nothing for a plan to be consistent with, and a Wireframe
    heading reading 'no screens' on every backend story is noise."""
    _patch_wireframe(
        monkeypatch, json.dumps({"no_ui_surface": True, "reason": "a migration"})
    )
    assert _section("plan") == ""


def test_other_run_kinds_get_no_section(monkeypatch):
    _patch_wireframe(monkeypatch, json.dumps(DECLARATION))
    for kind in ("prd", "breakdown", "test", "release", "deploy", "elaborate"):
        assert _section(kind) == "", f"{kind} should not carry a wireframe section"


def test_a_feature_code_run_gets_one_block_per_story(monkeypatch):
    """US-22.9's multi-story code run: each story's screens under its own
    display id, so the agent can tell them apart."""
    _patch_wireframe(monkeypatch, json.dumps(DECLARATION))
    section = _section(
        "code",
        stories=[
            {"issue_id": "a", "display_id": "US-4.2"},
            {"issue_id": "b", "display_id": "US-4.3"},
        ],
    )
    assert "### US-4.2" in section
    assert "### US-4.3" in section


def test_a_malformed_declaration_does_not_break_the_brief(monkeypatch):
    _patch_wireframe(monkeypatch, "not json at all")
    assert _section("plan") == ""


# ---------------------------------------------------------------------------
# US-54.3: completing the run touches no issue status
# ---------------------------------------------------------------------------


def test_completing_the_run_touches_no_issue_status():
    """The drawing has no approval gate (US-48.2), so the story is not the
    run's to move. Before us-54.3 this fell through to the default branch and
    stamped `in-review` onto a story whose code run was mid-build — the
    review page then dressed the merge gate over a run with no diff and no
    PR. Same contract, same test shape, as us-43.6's guidelines fix."""
    complete = DB_PY.split("def complete_run")[1].split("\ndef ")[0]
    branch = complete.split('elif kind == "wireframe":')[1].split("elif")[0]
    code = "\n".join(
        line for line in branch.splitlines() if not line.strip().startswith("#")
    )
    assert "issue_status = None" in code
    assert "in-review" not in code
    assert "wireframe-ready" in code
