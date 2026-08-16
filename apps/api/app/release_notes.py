"""The vocabulary a release's notes and checklist are written in (Phase 101).

Pure: no database, no settings, no network. Two things live here and they are
deliberately in one file —

  * the SECTIONS a checklist is ordered by (us-101.2), and
  * the BLOCKS a notes declaration is built from (us-101.4),

together with `vocabulary_brief()`, which describes both by reading them.
The brief is what the release instruction quotes, so the text telling an agent
what to write and the renderer drawing it cannot describe different things —
the `wireframes.vocabulary_brief()` pattern, and it exists because the
alternative is prose about a renderer that has since changed.

The coercion rule throughout is US-42.1's: coerce a field's shape, never
reject the payload over it. A hand-back refused on shape costs a full agent
run, and the agent has already done the thinking. What us-101.3 *does* refuse
is a check with nothing behind it — but that is a content rule, not a shape
one, and it is the sort of thing a re-read genuinely fixes.
"""

import re
from typing import Any

# ------------------------------------------------------------- migrations

"""us-101.1/101.5: a changed path is a migration if it is a .sql file in a
directory named for migrations.

Deliberately not this repository's own `infra/supabase/migrations/` — every
project the factory serves has its own layout, and a rule that only recognises
Build Mill's would answer "no migrations" for all of them."""
_MIGRATION_PATH = re.compile(r"(^|/)(migrations?|migrate)/[^/]*\.sql$", re.IGNORECASE)


def migration_paths(files: list[dict[str, Any]]) -> list[str]:
    """The migrations among a compare's changed files, in the order given."""
    return [
        p
        for p in (str(f.get("filename") or "") for f in files or [])
        if _MIGRATION_PATH.search(p)
    ]


# --------------------------------------------------------------- sections

"""us-101.2: the running order of a UAT session.

The order IS the instruction — "the happy path first, because every refusal
below assumes the happy path's object exists" only means something if the list
is in that order. Free text on the column, though: a section the agent invents
is appended after these rather than refused, because a release that genuinely
needs "Data migration" should get one.
"""
SECTIONS: tuple[tuple[str, str, str], ...] = (
    (
        "pre-flight",
        "Pre-flight",
        "Two minutes. Confirms the build is actually serving before anyone "
        "invests in a longer check.",
    ),
    (
        "happy-path",
        "The happy path",
        "The thing this release was built to do, end to end, in the order a "
        "person would do it.",
    ),
    (
        "refusals",
        "The refusals",
        "Each guard, one at a time. A refusal must be a sentence you can act "
        "on — a bare 400 or a stack trace is a failure.",
    ),
    (
        "regression",
        "Regression",
        "What this release touched that belongs to something else.",
    ),
    ("other", "Other", "Anything the sections above do not describe."),
)

SECTION_KEYS = tuple(k for k, _, _ in SECTIONS)
SECTION_LABELS = {k: label for k, label, _ in SECTIONS}
SECTION_NOTES = {k: note for k, _, note in SECTIONS}
DEFAULT_SECTION = "other"
INHERITED_SECTION = "regression"

_SECTION_ALIASES = {
    "preflight": "pre-flight",
    "pre flight": "pre-flight",
    "smoke": "pre-flight",
    "happy": "happy-path",
    "happy path": "happy-path",
    "happypath": "happy-path",
    "the happy path": "happy-path",
    "refusal": "refusals",
    "guards": "refusals",
    "errors": "refusals",
    "the refusals": "refusals",
    "regressions": "regression",
}


def normalize_section(value: Any) -> str:
    """A section key. Known names win; an invented one survives as a slug.

    Returning the invented name rather than folding it into `other` is the
    point: the agent chose that heading for a reason, and the renderer can
    show a heading it has never seen. Only the ORDER is the factory's."""
    raw = str(value or "").strip().lower()
    if not raw:
        return DEFAULT_SECTION
    if raw in SECTION_LABELS:
        return raw
    if raw in _SECTION_ALIASES:
        return _SECTION_ALIASES[raw]
    slug = "-".join(raw.replace("_", " ").replace("/", " ").split())
    return slug[:80] or DEFAULT_SECTION


def section_rank(key: str) -> int:
    """Known sections in their order; everything else after them."""
    try:
        return SECTION_KEYS.index(key)
    except ValueError:
        return len(SECTION_KEYS)


def section_label(key: str) -> str:
    if key in SECTION_LABELS:
        return SECTION_LABELS[key]
    return (key or "").replace("-", " ").strip().capitalize() or "Other"


# ----------------------------------------------------------------- blocks

"""us-101.4: what a notes declaration may contain.

Small on purpose. `prose` is the escape hatch — a fixed vocabulary will be the
wrong shape for some release, and a markdown block recovers nearly all of raw
HTML's range at none of its cost. A genuinely bespoke visual is a document,
not the notes page.
"""
BLOCKS: tuple[tuple[str, str], ...] = (
    ("prose", "Markdown. Anything the blocks below do not cover."),
    (
        "callout",
        "Something the reader must not miss, with `tone` one of info / warn / "
        "risk and a short `title`. Use risk for what could go wrong, not for "
        "emphasis.",
    ),
)
BLOCK_KINDS = tuple(k for k, _ in BLOCKS)
CALLOUT_TONES = ("info", "warn", "risk")


def as_declaration(value: Any) -> tuple[dict[str, Any], list[str]]:
    """`(declaration, findings)` — every plausible shape coerced, none refused.

    Findings are advice handed back with a successful submit, never a
    rejection: US-42.1's lesson, where fifteen consecutive hand-backs were
    422'd over a field typed as the wrong shape and each refusal cost a full
    re-run."""
    findings: list[str] = []
    if value is None or value == "":
        return {}, findings
    if isinstance(value, str):
        # A whole document sent as markdown rather than as a declaration is
        # not a mistake worth a re-run — it is one prose block.
        return {"standfirst": "", "sections": {}, "blocks": [
            {"block": "prose", "markdown": value.strip()}
        ]}, ["notes_doc arrived as text; it was kept as one prose block"]
    if not isinstance(value, dict):
        findings.append("notes_doc was not an object and was ignored")
        return {}, findings

    doc: dict[str, Any] = {}
    doc["standfirst"] = str(value.get("standfirst") or "").strip()

    sections = value.get("sections")
    out_sections: dict[str, str] = {}
    if isinstance(sections, dict):
        for k, v in sections.items():
            out_sections[normalize_section(k)] = str(v or "").strip()
    elif isinstance(sections, list):
        # [{"key": ..., "note": ...}] is just as plausible as a mapping.
        for entry in sections:
            if isinstance(entry, dict):
                key = normalize_section(entry.get("key") or entry.get("section"))
                out_sections[key] = str(entry.get("note") or "").strip()
        if sections:
            findings.append("notes_doc.sections was a list; it was keyed by section")
    elif sections is not None:
        findings.append("notes_doc.sections was not an object and was ignored")
    doc["sections"] = out_sections

    blocks = value.get("blocks")
    if isinstance(blocks, dict):
        blocks = [blocks]
    out_blocks: list[dict[str, Any]] = []
    for entry in blocks or []:
        if isinstance(entry, str):
            out_blocks.append({"block": "prose", "markdown": entry.strip()})
            continue
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("block") or "").strip().lower()
        if kind == "callout":
            tone = str(entry.get("tone") or "info").strip().lower()
            if tone not in CALLOUT_TONES:
                findings.append(
                    f"callout tone {tone!r} is not one of "
                    f"{', '.join(CALLOUT_TONES)}; it was shown as info"
                )
                tone = "info"
            out_blocks.append(
                {
                    "block": "callout",
                    "tone": tone,
                    "title": str(entry.get("title") or "").strip(),
                    "body": str(entry.get("body") or entry.get("markdown") or "").strip(),
                }
            )
            continue
        text = str(entry.get("markdown") or entry.get("text") or entry.get("body") or "")
        if kind and kind not in BLOCK_KINDS:
            findings.append(f"block {kind!r} is not in the vocabulary; it was kept as prose")
        if text.strip():
            out_blocks.append({"block": "prose", "markdown": text.strip()})
    doc["blocks"] = out_blocks

    if not (doc["standfirst"] or doc["sections"] or doc["blocks"]):
        return {}, findings
    return doc, findings


def summarize(doc: dict[str, Any]) -> str:
    """One line for a list or a log. Never the page itself."""
    if not doc:
        return ""
    parts = []
    if doc.get("standfirst"):
        parts.append(doc["standfirst"].splitlines()[0])
    n = len(doc.get("blocks") or [])
    if n:
        parts.append(f"{n} block(s)")
    return " · ".join(parts)


def _same_sentence(a: str, b: str) -> bool:
    def norm(s: str) -> str:
        return "".join(ch for ch in s.lower() if ch.isalnum())

    return bool(norm(a)) and norm(a) == norm(b)


def check_cases(
    cases: list[dict[str, Any]] | None,
    *,
    included: list[dict[str, Any]],
    inherited_display_ids: set[str],
    uncovered: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """us-101.3: `(normalized_cases, errors)`. Empty errors means it passes.

    Every error the hand-back has is collected and returned together. Stopping
    at the first one costs a whole agent run per rule, and an agent that fixes
    the missing steps only to discover the coverage floor on its next attempt
    has paid twice for one submission. This is the `report_merge_failure`
    house style: what comes back is the whole of what the next attempt knows.

    What is checked is structural — a check that is a title with nothing
    behind it, or a release that covers none of what it shipped. Whether an
    expected result is any GOOD is the manager's send-back, not a 422.
    """
    errors: list[str] = []
    by_display = {
        str(i.get("display_id") or "").strip().upper(): i
        for i in included
        if i.get("display_id")
    }
    named_uncovered = {str(u or "").strip().upper() for u in (uncovered or []) if str(u or "").strip()}
    for u in sorted(named_uncovered - set(by_display)):
        errors.append(
            f"`uncovered` names {u}, which is not in this release. "
            f"Included: {', '.join(sorted(by_display)) or 'nothing'}."
        )

    out: list[dict[str, Any]] = []
    covered: set[str] = set()
    for n, raw in enumerate(cases or [], start=1):
        case = raw if isinstance(raw, dict) else {}
        title = str(case.get("title") or "").strip()
        steps = str(case.get("steps") or "").strip()
        expected = str(case.get("expected_result") or "").strip()
        where = f"case {n}" + (f" ({title[:60]!r})" if title else "")

        if not title:
            errors.append(
                f"{where} has no title. A blank title used to be dropped "
                "silently, which is how a release shipped with fewer checks "
                "than it reported."
            )
        if not steps:
            errors.append(f"{where} has no `steps` — say what the tester should DO.")
        if not expected:
            errors.append(
                f"{where} has no `expected_result` — say what they should SEE. "
                "A check without one cannot be passed or failed by anybody but "
                "its author."
            )
        if expected and title and _same_sentence(expected, title):
            errors.append(
                f"{where} repeats its title as its expected result. The two "
                "halves are 'do this' and 'expect that', not the same sentence "
                "twice."
            )

        story = str(case.get("story") or case.get("display_id") or "").strip().upper()
        issue_id = None
        if story:
            item = by_display.get(story)
            if not item:
                errors.append(
                    f"{where} is tagged {story}, which is not in this release. "
                    f"Included: {', '.join(sorted(by_display)) or 'nothing'}."
                )
            else:
                issue_id = item.get("issue_id")
                covered.add(story)

        out.append(
            {
                "title": title,
                "steps": steps,
                "expected_result": expected,
                "section": normalize_section(case.get("section")),
                "sort": case.get("sort") if isinstance(case.get("sort"), int) else n,
                "critical": bool(case.get("critical")),
                "issue_id": issue_id,
            }
        )

    if not out:
        errors.append(
            "This release has no test cases. A release nobody can check is a "
            "release nobody can sign off — write the checks that prove what "
            "the included work items promised."
        )

    accounted = covered | {d.upper() for d in inherited_display_ids} | named_uncovered
    missing = sorted(set(by_display) - accounted)
    if missing and out:
        errors.append(
            "Nothing accounts for "
            + ", ".join(missing)
            + ". Each included work item needs a check you wrote (tag it with "
            "`story`), or a case it already carries, or a place in `uncovered` "
            "saying you left it deliberately."
        )
    return out, errors


def render_markdown(
    version: str,
    notes_summary: str,
    notes_detail: str,
    doc: dict[str, Any] | None,
    cases: list[dict[str, Any]] | None = None,
) -> str:
    """us-101.4: the exported `release-notes-<version>.md`, from the same
    declaration the page renders.

    Deliberately NOT a second authoring surface. It carries no deploy result
    and no suite counts, for the same reason the declaration has no slot for
    them: at the moment this is written, neither has happened."""
    out = [f"# Release {version}", "", (notes_summary or "").strip()]
    doc = doc or {}
    if doc.get("standfirst"):
        out += ["", doc["standfirst"].strip()]
    if (notes_detail or "").strip():
        out += ["", (notes_detail or "").strip()]

    for block in doc.get("blocks") or []:
        if block.get("block") == "callout":
            title = block.get("title") or block.get("tone", "").upper()
            out += ["", f"> **{title}**", ">", f"> {block.get('body', '')}"]
        else:
            out += ["", str(block.get("markdown") or "")]

    grouped: dict[str, list[dict[str, Any]]] = {}
    for c in cases or []:
        grouped.setdefault(c.get("section") or DEFAULT_SECTION, []).append(c)
    if grouped:
        out += ["", "## Checks", ""]
        for key in sorted(grouped, key=lambda k: (section_rank(k), k)):
            out += [f"### {section_label(key)}", ""]
            note = (doc.get("sections") or {}).get(key)
            if note:
                out += [note, ""]
            for c in sorted(
                grouped[key], key=lambda c: (c.get("sort") is None, c.get("sort"), c.get("title") or "")
            ):
                mark = " **(critical)**" if c.get("critical") else ""
                out.append(f"- **{c.get('title')}**{mark}")
                if c.get("steps"):
                    out.append(f"  - Do: {c['steps']}")
                if c.get("expected_result"):
                    out.append(f"  - Expect: {c['expected_result']}")
            out.append("")
    return "\n".join(out).strip() + "\n"


def vocabulary_brief() -> str:
    """What an agent is told it may write, generated from what is drawn.

    Quoted by the `release` instruction (us-101.6) so the two cannot drift."""
    lines = [
        "## Sections",
        "",
        "Put every check in one of these. They are shown in this order, and "
        "the order is the instruction — a tester works top to bottom.",
        "",
    ]
    for key, label, note in SECTIONS:
        lines.append(f"- `{key}` — **{label}**. {note}")
    lines += [
        "",
        "A section of your own is allowed and is shown after these. Prefer "
        "one of the above.",
        "",
        "## Notes blocks",
        "",
        "`notes_doc` is `{standfirst, sections, blocks}`. `standfirst` is a "
        "line or two on how to work through the release; `sections` maps a "
        "section key to a note explaining what that part is for; `blocks` is "
        "a list of:",
        "",
    ]
    for kind, note in BLOCKS:
        lines.append(f"- `{kind}` — {note}")
    lines += [
        "",
        "Do NOT write the deploy result, its duration, or automated test "
        "counts. None of them exist yet when you write — the UAT deploy is "
        "fired by your own hand-back, after it succeeds. The page fills them "
        "in itself.",
    ]
    return "\n".join(lines)
