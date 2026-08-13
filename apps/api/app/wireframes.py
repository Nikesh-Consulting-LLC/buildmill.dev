"""The wireframe tree (`docs/wireframes/`) — US-48.1.

What the factory writes into a project's repository when an agent draws a
story: one HTML page per work item, plus the kit every page renders through.

Everything in here is pure. Reading a project's stylesheet, fetching what is
already in the repo and committing the result belong to the callers
(`factory_mcp.py` on hand-back, `routers/projects.py` on sync); the builder
itself takes text in and gives text back, so it is testable without a GitHub
call and produces byte-identical files whichever caller ran it.

Deliberately NOT under `docs/factory/`: that tree is regenerated wholesale and
US-22.1 deletes anything a generation stops producing. Two roots, two writers,
and neither can delete the other's files.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

WIREFRAMES_ROOT = "docs/wireframes"
KIT_ROOT = f"{WIREFRAMES_ROOT}/_kit"

_KIT_DIR = Path(__file__).parent / "wireframe_kit"

# The custom properties the kit renders through. A project's stylesheet is
# read for exactly these; anything else it defines is its own business and is
# not copied into a wireframe.
TOKEN_NAMES = (
    "background",
    "foreground",
    "card",
    "card-foreground",
    "popover",
    "popover-foreground",
    "primary",
    "primary-foreground",
    "secondary",
    "secondary-foreground",
    "muted",
    "muted-foreground",
    "accent",
    "accent-foreground",
    "destructive",
    "border",
    "input",
    "ring",
    "chart-1",
    "chart-2",
    "chart-3",
    "chart-4",
    "chart-5",
    "radius",
    "sidebar",
    "sidebar-foreground",
    "sidebar-primary",
    "sidebar-primary-foreground",
    "sidebar-accent",
    "sidebar-accent-foreground",
    "sidebar-border",
    "sidebar-ring",
)

# Where a project's tokens are most likely to live, most specific first. The
# caller fetches these paths; whichever comes back first with a `:root` block
# defining any of TOKEN_NAMES wins.
TOKEN_SOURCE_CANDIDATES = (
    "apps/web/src/app/globals.css",
    "src/app/globals.css",
    "app/globals.css",
    "src/styles/globals.css",
    "styles/globals.css",
    "src/index.css",
    "src/app.css",
    "app/styles/globals.css",
    "globals.css",
)

# The component names an agent may declare. Kept in sync with COMPONENTS in
# kit.js by `test_wireframe_kit.py`, which parses the JS rather than trusting
# this list — two hand-maintained vocabularies would drift the first time one
# of them gained a component.
COMPONENTS = (
    "row",
    "stack",
    "grid",
    "page-header",
    "card",
    "button",
    "badge",
    "status-badge",
    "table",
    "field",
    "input",
    "textarea",
    "select",
    "checkbox",
    "tabs",
    "dialog",
    "empty-state",
    "toast",
    "avatar",
    "separator",
    "skeleton",
    "menu",
    "text",
    "link",
)

STATES = ("populated", "empty", "loading", "error")


# ---------------------------------------------------------------------------
# The kit
# ---------------------------------------------------------------------------


def kit_asset(name: str) -> str:
    """One static kit file's contents, from the package directory."""
    return (_KIT_DIR / name).read_text(encoding="utf-8")


def kit_files(tokens_css: str | None = None) -> dict[str, str]:
    """The kit as `{path: content}`, ready for `repo_docs.commit_files`.

    `tokens_css` is a project's extracted tokens; without it the neutral
    default ships, which is the honest rendering of "this project has no
    design system to match"."""
    return {
        f"{KIT_ROOT}/kit.css": kit_asset("kit.css"),
        f"{KIT_ROOT}/kit.js": kit_asset("kit.js"),
        f"{KIT_ROOT}/tokens.css": tokens_css or kit_asset("tokens.default.css"),
    }


def kit_code_hash() -> str:
    """The kit's *code* version — kit.css and kit.js only.

    Deliberately excludes tokens.css: a project's tokens are read from its own
    repository, and making them part of the version would mean every hand-back
    had to re-read the stylesheet just to decide whether to skip the push."""
    digest = hashlib.sha256()
    for name in ("kit.css", "kit.js"):
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(kit_asset(name).encode())
        digest.update(b"\0")
    return digest.hexdigest()


def kit_hash(tokens_css: str | None = None) -> str:
    """US-22.7's shape: what the project last received, so a hand-back that
    would push an identical kit costs no GitHub call."""
    files = kit_files(tokens_css)
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.encode())
        digest.update(b"\0")
        digest.update(files[path].encode())
        digest.update(b"\0")
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Token extraction
# ---------------------------------------------------------------------------


def _top_level_blocks(css: str) -> list[tuple[str, str]]:
    """`(selector, body)` for every brace block at depth 0.

    Written as a scanner rather than a regex because Tailwind v4's `@theme`
    blocks nest `@keyframes`, and a non-greedy regex would close the outer
    block on the inner brace and hand back a body that is not the block."""
    blocks: list[tuple[str, str]] = []
    depth = 0
    selector_start = 0
    body_start = 0
    selector = ""
    for index, char in enumerate(css):
        if char == "{":
            if depth == 0:
                selector = css[selector_start:index].strip()
                body_start = index + 1
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                blocks.append((selector, css[body_start:index]))
                selector_start = index + 1
            elif depth < 0:
                # Unbalanced input — stop rather than misattribute the rest.
                break
    return blocks


def _declarations(body: str) -> dict[str, str]:
    """`--name: value` pairs in a block body, ignoring nested rules."""
    found: dict[str, str] = {}
    for match in re.finditer(r"(--[A-Za-z0-9_-]+)\s*:\s*([^;{}]+)", body):
        found[match.group(1)[2:]] = match.group(2).strip()
    return found


def _is_dark_selector(selector: str) -> bool:
    lowered = selector.lower()
    return (
        ".dark" in lowered
        or 'data-theme="dark"' in lowered
        or "data-theme='dark'" in lowered
        or "[data-mode=dark]" in lowered
    )


def read_tokens(css: str) -> tuple[dict[str, str], dict[str, str]]:
    """`(light, dark)` token values found in one stylesheet.

    Only TOKEN_NAMES are taken, and only values that are not themselves
    `var(...)` indirections — Tailwind v4's `@theme inline` block maps
    `--color-primary: var(--primary)`, and copying that into a wireframe would
    produce a page whose every colour resolves to nothing."""
    light: dict[str, str] = {}
    dark: dict[str, str] = {}
    wanted = set(TOKEN_NAMES)
    for selector, body in _top_level_blocks(css):
        stripped = selector.strip()
        if stripped.startswith("@"):
            continue
        target: dict[str, str] | None = None
        if stripped == ":root" or stripped.startswith(":root"):
            target = light
        elif _is_dark_selector(stripped):
            target = dark
        if target is None:
            continue
        for name, value in _declarations(body).items():
            if name in wanted and not value.startswith("var("):
                target[name] = value
    return light, dark


def _render_token_block(selector: str, values: dict[str, str]) -> str:
    lines = [f"{selector} {{"]
    for name in TOKEN_NAMES:
        if name in values:
            lines.append(f"  --{name}: {values[name]};")
    lines.append("}")
    return "\n".join(lines)


def build_tokens_css(css: str | None, source_path: str | None) -> tuple[str, str]:
    """`(tokens.css, provenance)` for a project.

    Falls back to the neutral default whenever a stylesheet was not found or
    did not define enough of the contract to be worth using — and says which
    case applied in the file's own header, so nobody has to guess why a
    wireframe looks generic."""
    light: dict[str, str] = {}
    dark: dict[str, str] = {}
    if css:
        light, dark = read_tokens(css)

    # A stylesheet that defines a handful of unrelated properties is not a
    # design system. Below this bar the neutral default is the better answer.
    if len(light) < 6:
        reason = (
            f"no design tokens found in {source_path}"
            if source_path
            else "no token source found in this repository"
        )
        header = (
            "/* Build Mill wireframe kit — design tokens\n"
            f" *\n * Source: none — {reason}.\n"
            " * These are the neutral defaults; a wireframe drawn against them\n"
            " * looks like nothing in particular, which is the honest rendering\n"
            " * of a project with no design system to match.\n */\n\n"
        )
        return header + kit_asset("tokens.default.css").split("*/", 1)[1].lstrip(), reason

    header = (
        "/* Build Mill wireframe kit — design tokens\n"
        " *\n"
        f" * Source: {source_path}\n"
        " *\n"
        " * Generated from the project's own stylesheet. Edit the source, not\n"
        " * this file — a wireframe sync regenerates it.\n"
        " */\n\n"
    )
    parts = [header, _render_token_block(":root", light)]
    if dark:
        parts.append("\n\n" + _render_token_block(".dark", dark))

    # Fonts are never copied: a wireframe must render from disk with no
    # network, and a project's --font-sans is almost always a next/font handle
    # that resolves to nothing outside the app.
    parts.append(
        "\n\n/* Fonts are never fetched — see tokens.default.css. */\n"
        ":root {\n"
        "  --wf-font-sans:\n"
        "    ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto,"
        " Helvetica, Arial, sans-serif;\n"
        "  --wf-font-mono: ui-monospace, SFMono-Regular, 'SF Mono', Menlo,"
        " Consolas, monospace;\n"
        "  --wf-font-heading: var(--wf-font-sans);\n"
        "}\n"
    )
    return "".join(parts), f"read from {source_path}"


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


def page_path(display_id: str) -> str:
    """`docs/wireframes/us-4.2.html` — the id, never the title (US-22.2)."""
    slug = re.sub(r"[^a-z0-9.-]+", "-", str(display_id).strip().lower()).strip("-")
    return f"{WIREFRAMES_ROOT}/{slug or 'unknown'}.html"


def _escape(text: Any) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _embed(declaration: dict[str, Any]) -> str:
    """JSON safe to sit inside a <script> element.

    `</script>` anywhere in the data — a story about the wireframe kit itself
    would contain one — closes the block early and silently truncates the
    page, so the sequence is escaped at the slash."""
    text = json.dumps(declaration, indent=2, ensure_ascii=False, sort_keys=False)
    return text.replace("</", "<\\/")


def _inline_js(source: str) -> str:
    """JS safe to inline in a <script> element.

    The HTML parser ends a script at the literal `</script`, wherever it sits —
    including inside a JS string. kit.js talks about the declaration block in
    its own error messages, so this is one edit away from being true."""
    return re.sub(r"</(script)", r"<\\/\1", source, flags=re.IGNORECASE)


def build_page(
    display_id: str,
    title: str,
    declaration: dict[str, Any],
    *,
    depth: int = 1,
) -> str:
    """One wireframe page. `depth` is how many folders down from
    `docs/wireframes/` the page sits, so the kit link stays relative and the
    page opens from disk."""
    prefix = "_kit" if depth == 1 else "/".join([".."] * (depth - 1) + ["_kit"])
    doc = dict(declaration or {})
    doc.setdefault("story", display_id)
    doc.setdefault("title", title)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_escape(display_id)} — {_escape(title)}</title>
<link rel="stylesheet" href="{prefix}/tokens.css">
<link rel="stylesheet" href="{prefix}/kit.css">
</head>
<body>
<script type="application/wireframe+json">
{_embed(doc)}
</script>
<script src="{prefix}/kit.js"></script>
<noscript>
This wireframe is a declaration rendered by _kit/kit.js. Enable JavaScript, or
read the JSON above.
</noscript>
</body>
</html>
"""


def build_preview(
    display_id: str,
    title: str,
    declaration: dict[str, Any],
    tokens_css: str | None = None,
) -> str:
    """The same page, self-contained — kit inlined, nothing linked.

    What the app's own wireframe panel renders in a sandboxed iframe. It has
    to be one document because the frame is sandboxed WITHOUT
    `allow-same-origin`, so its origin is opaque and it can resolve no
    relative URL back to the app.

    Deliberately the same `kit.js` and `kit.css` the repository gets — a
    second renderer in the web app would be a second thing to keep in step,
    and the manager would be approving a picture the repo does not draw."""
    doc = dict(declaration or {})
    doc.setdefault("story", display_id)
    doc.setdefault("title", title)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_escape(display_id)} — {_escape(title)}</title>
<style>
{tokens_css or kit_asset("tokens.default.css")}
</style>
<style>
{kit_asset("kit.css")}
</style>
</head>
<body>
<script type="application/wireframe+json">
{_embed(doc)}
</script>
<script>
{_inline_js(kit_asset("kit.js"))}
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Reading a declaration back
# ---------------------------------------------------------------------------


def _walk(nodes: Any, seen: set[str]) -> None:
    for node in nodes if isinstance(nodes, list) else [nodes]:
        if not isinstance(node, dict):
            continue
        name = node.get("component") or node.get("type")
        if isinstance(name, str) and name:
            seen.add(name)
        for key in ("children", "actions", "footer", "rows", "topbar", "regions"):
            value = node.get(key)
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, (dict, list)):
                        _walk(item, seen)
            elif isinstance(value, dict):
                _walk(value, seen)
        for key in ("action", "emptyAction"):
            if isinstance(node.get(key), dict):
                _walk(node[key], seen)


def declared_screens(declaration: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The compact summary a plan run reads (US-48.4).

    Screens, routes, the components each one uses, the states it covers and
    the acceptance criteria it annotates — deliberately not the HTML. Phase 38
    measured what a plan run already reads; a 40 KB page in every plan context
    would make that worse for no gain."""
    out: list[dict[str, Any]] = []
    for screen in (declaration or {}).get("screens") or []:
        if not isinstance(screen, dict):
            continue
        components: set[str] = set()
        _walk(screen.get("regions") or [], components)
        _walk(screen.get("topbar") or [], components)
        acs: set[str] = set()
        _collect_acs(screen.get("regions") or [], acs)
        out.append(
            {
                "name": screen.get("name") or "Screen",
                "route": screen.get("route"),
                "shell": screen.get("shell") or "app",
                "states": [s for s in screen.get("states") or [] if s in STATES]
                or ["populated"],
                "components": sorted(components),
                "acceptance_criteria": sorted(acs, key=_ac_sort),
                "note": screen.get("note"),
            }
        )
    return out


def _ac_sort(value: str) -> tuple[int, str]:
    try:
        return (0, f"{int(value):04d}")
    except (TypeError, ValueError):
        return (1, str(value))


def _collect_acs(nodes: Any, seen: set[str]) -> None:
    for node in nodes if isinstance(nodes, list) else [nodes]:
        if not isinstance(node, dict):
            continue
        ac = node.get("ac")
        if ac is not None:
            for item in ac if isinstance(ac, list) else [ac]:
                seen.add(str(item))
        for key in ("children", "actions", "footer", "rows", "regions"):
            value = node.get(key)
            if isinstance(value, (list, dict)):
                _collect_acs(value, seen)
        for key in ("action", "emptyAction"):
            if isinstance(node.get(key), dict):
                _collect_acs(node[key], seen)


def summarize(declaration: dict[str, Any] | None) -> str:
    """One line for a list or a card: "3 screens · 4 states · 12 components"."""
    screens = declared_screens(declaration)
    if not screens:
        return "no screens"
    states = {state for screen in screens for state in screen["states"]}
    components = {c for screen in screens for c in screen["components"]}
    return (
        f"{len(screens)} screen{'s' if len(screens) != 1 else ''} · "
        f"{len(states)} state{'s' if len(states) != 1 else ''} · "
        f"{len(components)} component{'s' if len(components) != 1 else ''}"
    )


# ---------------------------------------------------------------------------
# The tree (US-48.5)
# ---------------------------------------------------------------------------


def _index_declaration(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """The index page, expressed in the kit's own vocabulary so it restyles
    with every other page instead of being a second hand-written document."""
    drawn = [e for e in entries if not e["no_ui_surface"]]
    verdicts = [e for e in entries if e["no_ui_surface"]]

    regions: list[dict[str, Any]] = [
        {
            "component": "page-header",
            "title": "Wireframes",
            "description": (
                f"{len(drawn)} drawn"
                + (f" · {len(verdicts)} with no user-visible surface" if verdicts else "")
                if entries
                else "Nothing has been drawn yet."
            ),
        }
    ]

    # Grouped by feature, in build order. A flat list of us-4.7.html files is
    # not a place anyone browses a feature's screens.
    by_feature: dict[str, list[dict[str, Any]]] = {}
    for entry in drawn:
        by_feature.setdefault(entry["feature"] or "Unparented stories", []).append(entry)

    for feature, items in by_feature.items():
        regions.append(
            {
                "component": "card",
                "title": feature,
                "children": [
                    {
                        "component": "table",
                        "columns": ["Story", "Title", "Screens"],
                        "rows": [
                            [
                                {
                                    "component": "link",
                                    "label": item["display_id"],
                                    # Relative to docs/wireframes/, where
                                    # index.html sits — so the tree navigates
                                    # from a checkout, with no server and no
                                    # absolute paths.
                                    "href": item["display_id"].lower() + ".html",
                                },
                                item["title"],
                                ", ".join(s["name"] for s in item["screens"]) or "—",
                            ]
                            for item in items
                        ],
                        "empty": "No wireframes in this feature",
                    }
                ],
            }
        )

    if verdicts:
        # Named, not hidden: the index has to answer "was this asked?" and not
        # only "was this drawn?".
        regions.append(
            {
                "component": "card",
                "title": "No user-visible surface",
                "description": "Asked, and answered — these stories change nothing a user sees.",
                "children": [
                    {
                        "component": "table",
                        "columns": ["Story", "Title", "Reason"],
                        "rows": [
                            [e["display_id"], e["title"], e["reason"] or "—"]
                            for e in verdicts
                        ],
                        "empty": "—",
                    }
                ],
            }
        )

    if not entries:
        regions.append(
            {
                "component": "empty-state",
                "title": "No wireframes yet",
                "description": (
                    "Ask an agent to draw a story in Build Mill and it will "
                    "appear here."
                ),
            }
        )

    return {
        "story": "Wireframes",
        "title": "Every screen this project has drawn",
        "screens": [
            {
                "name": "Wireframes",
                "shell": "bare",
                "states": ["populated"],
                "regions": regions,
            }
        ],
    }


def build_tree(entries: list[dict[str, Any]]) -> dict[str, str]:
    """The whole wireframe tree as `{path: content}` — pure, so tests need no
    GitHub call and the sync and the per-hand-back write produce byte-identical
    files.

    `entries` is one dict per work item that has been drawn or answered:
    `display_id`, `title`, `feature`, `declaration`. The kit is NOT included —
    it is versioned separately and pushed only when it differs."""
    files: dict[str, str] = {
        f"{WIREFRAMES_ROOT}/README.md": kit_asset("README.md"),
    }
    normalized: list[dict[str, Any]] = []
    for entry in entries:
        declaration = entry.get("declaration") or {}
        display_id = entry.get("display_id") or ""
        if not display_id:
            continue
        no_ui = bool(declaration.get("no_ui_surface"))
        normalized.append(
            {
                "display_id": display_id,
                "title": entry.get("title") or "",
                "feature": entry.get("feature"),
                "no_ui_surface": no_ui,
                "reason": declaration.get("reason"),
                "screens": declared_screens(declaration),
            }
        )
        if not no_ui and declaration.get("screens"):
            files[page_path(display_id)] = build_page(
                display_id, entry.get("title") or "", declaration
            )

    files[f"{WIREFRAMES_ROOT}/index.html"] = build_page(
        "Wireframes", "Every screen this project has drawn", _index_declaration(normalized)
    )
    return files


def vocabulary_brief() -> str:
    """What an agent is told it may declare. Assembled from COMPONENTS so the
    instruction and the renderer cannot describe different kits."""
    return (
        "Declare the screen as JSON, not as HTML or CSS. Components: "
        + ", ".join(COMPONENTS)
        + ". States: "
        + ", ".join(STATES)
        + "."
    )
