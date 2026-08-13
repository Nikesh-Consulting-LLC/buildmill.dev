"""US-48.1 — the wireframe kit.

The kit ships no endpoint and no run kind: what it ships is a contract. These
tests are that contract — the vocabulary the renderer and the agent
instruction must agree on, the token extraction, and the page shape.
"""

import json
import re

import pytest

from app import wireframes


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


def _kit_js() -> str:
    return wireframes.kit_asset("kit.js")


def test_components_match_the_renderer():
    """`wireframes.COMPONENTS` is what the agent is told it may declare, and
    `COMPONENTS` in kit.js is what will actually render. Two hand-maintained
    lists drift the first time one of them gains a component, so this parses
    the JS rather than trusting the Python."""
    body = _kit_js().split("const COMPONENTS = {", 1)[1]
    depth = 1
    end = 0
    for index, char in enumerate(body):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index
                break
    block = body[:end]
    # Top-level method names only: `name(spec, ctx) {` or `'name'(spec) {`.
    rendered = set(
        re.findall(r"^  '?([a-z-]+)'?\([a-z, ]*\) \{", block, flags=re.MULTILINE)
    )
    assert rendered == set(wireframes.COMPONENTS), (
        f"only in kit.js: {sorted(rendered - set(wireframes.COMPONENTS))}; "
        f"only in wireframes.py: {sorted(set(wireframes.COMPONENTS) - rendered)}"
    )


def test_vocabulary_brief_names_every_component():
    brief = wireframes.vocabulary_brief()
    for name in wireframes.COMPONENTS:
        assert name in brief
    for state in wireframes.STATES:
        assert state in brief


def test_component_names_mirror_the_apps_own_files():
    """The point of the vocabulary is that a coder reading a wireframe can
    grep the name and find the component. A rename here that does not exist in
    apps/web breaks that, silently."""
    for name in (
        "card",
        "button",
        "badge",
        "table",
        "input",
        "textarea",
        "select",
        "checkbox",
        "tabs",
        "dialog",
        "avatar",
        "separator",
        "skeleton",
        "toast",
    ):
        assert name in wireframes.COMPONENTS
    # The two shared patterns CLAUDE.md names as mandatory.
    assert "status-badge" in wireframes.COMPONENTS
    assert "empty-state" in wireframes.COMPONENTS


# ---------------------------------------------------------------------------
# The kit as files
# ---------------------------------------------------------------------------


def test_kit_files_are_three_under_the_kit_root():
    files = wireframes.kit_files()
    assert set(files) == {
        "docs/wireframes/_kit/kit.css",
        "docs/wireframes/_kit/kit.js",
        "docs/wireframes/_kit/tokens.css",
    }
    assert all(content.strip() for content in files.values())


def test_kit_fetches_nothing():
    """A wireframe must render from disk with no network. An @import, a font
    URL or a fetch in the kit breaks that for every page at once."""
    for name in ("kit.css", "kit.js", "tokens.default.css"):
        text = wireframes.kit_asset(name)
        assert "@import" not in text
        assert "http://" not in text
        assert "https://" not in text
        assert "fetch(" not in text
        assert "XMLHttpRequest" not in text


def test_kit_css_hardcodes_no_colour():
    """Every colour resolves through the token contract, so a project's own
    palette is the only thing that decides what a wireframe looks like."""
    css = wireframes.kit_asset("kit.css")
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css)
    assert not re.search(r"\brgb a?\(", css)
    # oklch() may only appear in the token files, never in the kit's styling.
    assert "oklch(" not in css


def test_kit_hash_changes_with_tokens_and_is_stable():
    base = wireframes.kit_hash()
    assert base == wireframes.kit_hash()
    assert wireframes.kit_hash(":root { --primary: red; }") != base


# ---------------------------------------------------------------------------
# Token extraction
# ---------------------------------------------------------------------------


APP_GLOBALS = """
@import "tailwindcss";

@theme inline {
  --color-primary: var(--primary);
  --radius-sm: calc(var(--radius) * 0.6);
}

@theme {
  --animate-belt: belt 0.9s linear infinite;

  @keyframes belt {
    to {
      stroke-dashoffset: -12;
    }
  }
}

:root {
  --background: oklch(1 0 0);
  --foreground: oklch(0.145 0 0);
  --card: oklch(1 0 0);
  --primary: oklch(0.205 0 0);
  --primary-foreground: oklch(0.985 0 0);
  --muted-foreground: oklch(0.556 0 0);
  --border: oklch(0.922 0 0);
  --radius: 0.625rem;
  --font-sans: var(--font-geist-sans);
}

.dark {
  --background: oklch(0.145 0 0);
  --foreground: oklch(0.985 0 0);
  --border: oklch(1 0 0 / 10%);
}
"""


def test_read_tokens_finds_light_and_dark():
    light, dark = wireframes.read_tokens(APP_GLOBALS)
    assert light["background"] == "oklch(1 0 0)"
    assert light["radius"] == "0.625rem"
    assert dark["background"] == "oklch(0.145 0 0)"
    assert dark["border"] == "oklch(1 0 0 / 10%)"


def test_read_tokens_skips_var_indirections_and_at_rules():
    """Tailwind v4's `@theme inline` maps --color-primary: var(--primary).
    Copying that into a wireframe produces a page whose every colour resolves
    to nothing."""
    light, _ = wireframes.read_tokens(APP_GLOBALS)
    assert "color-primary" not in light
    assert all(not value.startswith("var(") for value in light.values())
    # The nested @keyframes inside @theme must not be mistaken for a block
    # that closes @theme early.
    assert "animate-belt" not in light


def test_read_tokens_survives_the_real_globals_css():
    """The AC names this file: run against it, the extractor reproduces its
    :root values."""
    from pathlib import Path

    globals_css = (
        Path(__file__).resolve().parents[3]
        / "apps"
        / "web"
        / "src"
        / "app"
        / "globals.css"
    )
    if not globals_css.exists():  # pragma: no cover - repo layout guard
        pytest.skip("apps/web not present")
    light, dark = wireframes.read_tokens(globals_css.read_text(encoding="utf-8"))
    assert light["background"] == "oklch(1 0 0)"
    assert light["primary"] == "oklch(0.205 0 0)"
    assert light["radius"] == "0.625rem"
    assert light["sidebar-border"] == "oklch(0.922 0 0)"
    assert dark["background"] == "oklch(0.145 0 0)"
    assert dark["destructive"] == "oklch(0.704 0.191 22.216)"
    # Every token the kit renders through is present.
    for name in wireframes.TOKEN_NAMES:
        assert name in light, f"{name} missing from the extracted light tokens"


def test_build_tokens_css_records_its_source():
    css, provenance = wireframes.build_tokens_css(APP_GLOBALS, "app/globals.css")
    assert "app/globals.css" in css
    assert "app/globals.css" in provenance
    assert ":root {" in css
    assert ".dark {" in css
    assert "--wf-font-sans" in css
    # Fonts are never copied — a next/font handle resolves to nothing here.
    assert "--font-sans: var(--font-geist-sans)" not in css


def test_build_tokens_css_falls_back_and_says_so():
    css, provenance = wireframes.build_tokens_css(None, None)
    assert "no token source found" in css
    assert "no token source found" in provenance
    assert "--background" in css
    assert "--wf-font-sans" in css


def test_build_tokens_css_ignores_a_stylesheet_that_is_not_a_design_system():
    css, provenance = wireframes.build_tokens_css(
        ":root { --brand-blue: #0af; --gutter: 12px; }", "src/index.css"
    )
    assert "no design tokens found in src/index.css" in provenance
    assert "--background" in css


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


DECLARATION = {
    "screens": [
        {
            "name": "Work items",
            "route": "/issues",
            "states": ["populated", "empty", "loading"],
            "sidebar": {"brand": "Build Mill", "items": [{"label": "Work items"}]},
            "regions": [
                {
                    "component": "page-header",
                    "title": "Work items",
                    "actions": [{"component": "button", "label": "Define"}],
                    "ac": 1,
                },
                {
                    "component": "card",
                    "title": "Queue",
                    "children": [
                        {
                            "component": "table",
                            "columns": ["Story", "Status"],
                            "rows": [
                                [
                                    "US-4.1",
                                    {"component": "status-badge", "label": "merged"},
                                ]
                            ],
                            "empty": "Nothing queued",
                            "ac": [2, 3],
                        }
                    ],
                },
            ],
        }
    ]
}


def test_page_path_uses_the_id_never_the_title():
    assert wireframes.page_path("US-4.2") == "docs/wireframes/us-4.2.html"
    assert wireframes.page_path("US-13.10") == "docs/wireframes/us-13.10.html"


def test_build_page_links_the_kit_relatively():
    html = wireframes.build_page("US-4.2", "A story", DECLARATION)
    assert '<link rel="stylesheet" href="_kit/tokens.css">' in html
    assert '<link rel="stylesheet" href="_kit/kit.css">' in html
    assert '<script src="_kit/kit.js"></script>' in html
    assert "US-4.2" in html


def test_the_kit_is_never_loaded_as_a_module():
    """A browser refuses to load a module script over file:// — module
    scripts are fetched with CORS and a file:// origin is opaque. `type=
    "module"` here, or an `export` in kit.js, breaks "opens from disk" for
    every wireframe at once, and it breaks it silently: the page renders
    blank with one console error."""
    html = wireframes.build_page("US-4.2", "A story", DECLARATION)
    assert 'type="module"' not in html
    js = _kit_js()
    assert not re.search(r"^\s*export\s", js, flags=re.MULTILINE)
    assert not re.search(r"^\s*import\s", js, flags=re.MULTILINE)


def test_build_page_embeds_a_parseable_declaration():
    html = wireframes.build_page("US-4.2", "A story", DECLARATION)
    block = html.split('<script type="application/wireframe+json">', 1)[1]
    block = block.split("</script>", 1)[0]
    parsed = json.loads(block.replace("<\\/", "</"))
    assert parsed["story"] == "US-4.2"
    assert parsed["screens"][0]["name"] == "Work items"


def test_build_page_cannot_be_truncated_by_its_own_content():
    """A wireframe *for the wireframe feature* contains the string
    `</script>`. Unescaped, it closes the block early and the page silently
    renders nothing."""
    html = wireframes.build_page(
        "US-48.1",
        "The kit",
        {"screens": [{"name": "x", "regions": [{"component": "text", "text": "</script>"}]}]},
    )
    assert html.count("</script>") == 2  # the declaration block and the module
    assert "<\\/script>" in html


def test_build_page_escapes_the_title():
    html = wireframes.build_page("US-1.1", 'A "<b>bold</b>" story', {"screens": []})
    assert "<b>bold</b>" not in html
    assert "&lt;b&gt;" in html


# ---------------------------------------------------------------------------
# Reading a declaration back (what US-48.4 feeds a plan run)
# ---------------------------------------------------------------------------


def test_declared_screens_summarizes_without_the_html():
    screens = wireframes.declared_screens(DECLARATION)
    assert len(screens) == 1
    screen = screens[0]
    assert screen["name"] == "Work items"
    assert screen["route"] == "/issues"
    assert screen["states"] == ["populated", "empty", "loading"]
    assert "table" in screen["components"]
    assert "status-badge" in screen["components"]
    assert "page-header" in screen["components"]
    assert screen["acceptance_criteria"] == ["1", "2", "3"]


def test_declared_screens_is_small_enough_for_a_plan_context():
    """The AC's measured bound: under 2 KB for a representative screen."""
    payload = json.dumps(wireframes.declared_screens(DECLARATION))
    assert len(payload) < 2048


def test_declared_screens_handles_an_empty_or_missing_declaration():
    assert wireframes.declared_screens(None) == []
    assert wireframes.declared_screens({}) == []
    assert wireframes.declared_screens({"screens": "not a list"}) == []


# ---------------------------------------------------------------------------
# US-48.5: the tree
# ---------------------------------------------------------------------------


ENTRIES = [
    {
        "display_id": "US-4.2",
        "title": "A manager can filter the queue",
        "feature": "FEAT-4.1 — The queue",
        "declaration": DECLARATION,
    },
    {
        "display_id": "US-4.3",
        "title": "The filter survives a reload",
        "feature": "FEAT-4.1 — The queue",
        "declaration": {"screens": [{"name": "Queue", "route": "/issues"}]},
    },
    {
        "display_id": "US-4.4",
        "title": "Index the filter column",
        "feature": "FEAT-4.1 — The queue",
        "declaration": {"no_ui_surface": True, "reason": "a migration only"},
    },
]


def test_build_tree_is_pure_and_writes_a_page_per_drawn_story():
    files = wireframes.build_tree(ENTRIES)
    assert "docs/wireframes/us-4.2.html" in files
    assert "docs/wireframes/us-4.3.html" in files
    # The no-UI verdict gets no page — there is no screen to render.
    assert "docs/wireframes/us-4.4.html" not in files
    assert "docs/wireframes/index.html" in files
    assert "docs/wireframes/README.md" in files
    # The kit is versioned separately and pushed only when it differs.
    assert not any(p.startswith("docs/wireframes/_kit/") for p in files)


def test_the_tree_and_the_hand_back_write_agree_byte_for_byte():
    """One writer, two callers. If these ever diverge, a sync silently
    rewrites every page it touches and the diff is unreadable."""
    files = wireframes.build_tree(ENTRIES[:1])
    direct = wireframes.build_page(
        "US-4.2", "A manager can filter the queue", DECLARATION
    )
    assert files["docs/wireframes/us-4.2.html"] == direct


def test_the_index_names_the_no_ui_verdicts_rather_than_hiding_them():
    """The index has to answer "was this asked?" and not only "was this
    drawn?" — otherwise an undrawn story and an answered one look identical."""
    index = wireframes.build_tree(ENTRIES)["docs/wireframes/index.html"]
    assert "US-4.4" in index
    assert "a migration only" in index
    assert "No user-visible surface" in index


def test_the_index_groups_by_feature_and_renders_through_the_kit():
    index = wireframes.build_tree(ENTRIES)["docs/wireframes/index.html"]
    assert "FEAT-4.1 — The queue" in index
    assert '_kit/kit.js' in index  # the same kit, so it restyles with them
    assert '"component": "table"' in index


def test_an_empty_project_gets_an_explained_tree_not_a_failure():
    files = wireframes.build_tree([])
    assert set(files) == {
        "docs/wireframes/README.md",
        "docs/wireframes/index.html",
    }
    assert "Nothing has been drawn yet" in files["docs/wireframes/index.html"]


def test_the_readme_states_the_ownership_in_the_file_itself():
    """Nobody should learn that hand-added files do not survive by losing
    one."""
    readme = wireframes.build_tree([])["docs/wireframes/README.md"]
    assert "owns this whole folder" in readme
    assert "does not survive" in readme
    assert "never titles" in readme


def test_the_index_links_each_story_relatively():
    """An index you cannot click is a list. The hrefs are relative to
    docs/wireframes/, where index.html sits, so the tree navigates from a
    plain checkout with no server and no absolute paths."""
    index = wireframes.build_tree(ENTRIES)["docs/wireframes/index.html"]
    assert '"component": "link"' in index
    assert '"href": "us-4.2.html"' in index
    assert '"href": "us-4.3.html"' in index
    # The no-UI verdict has no page, so it is named but never linked.
    assert '"href": "us-4.4.html"' not in index


def test_the_kit_refuses_an_absolute_link():
    """A wireframe reaches nothing off its own folder — an agent-authored
    href pointing at another origin would make a sketch a navigation surface
    into somewhere else."""
    js = _kit_js()
    assert "link href must be relative" in js
    assert "/^[a-z]+:/i.test(href)" in js


def test_an_entry_with_no_display_id_is_skipped_not_crashed():
    files = wireframes.build_tree([{"title": "x", "declaration": DECLARATION}])
    assert "docs/wireframes/index.html" in files
    assert not any(p.startswith("docs/wireframes/us-") for p in files)


def test_summarize_reads_as_one_line():
    assert wireframes.summarize(DECLARATION) == "1 screen · 3 states · 5 components"
    assert wireframes.summarize(None) == "no screens"
