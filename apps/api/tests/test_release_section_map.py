"""us-101.2: the section vocabulary has one home and one mirror, and they agree.

`release_notes.SECTIONS` is canonical; `apps/web/src/lib/release-sections.ts`
is a hand-kept copy because there is no build step between the two apps. This
parses both and fails in either direction — the us-99.1 pattern, which exists
because an unchecked mirror is how `run-kinds.ts` came to list seven run kinds
while the database allowed ten.
"""

import re
from pathlib import Path

from app import release_notes as rn

TS = (
    Path(__file__).resolve().parents[3]
    / "apps"
    / "web"
    / "src"
    / "lib"
    / "release-sections.ts"
)


def _ts_source() -> str:
    assert TS.exists(), f"the web mirror is missing: {TS}"
    return TS.read_text(encoding="utf-8")


def _ts_sections() -> list[str]:
    src = _ts_source()
    m = re.search(r"RELEASE_SECTIONS\s*=\s*\[(.*?)\]", src, re.DOTALL)
    assert m, "could not find RELEASE_SECTIONS in the mirror"
    keys = re.findall(r'"([^"]+)"', m.group(1))
    # Anchored on `= [` and asserted non-empty: a parser that silently returns
    # nothing compares equal to nothing and passes forever.
    assert keys, "parsed no sections out of the mirror"
    return keys


def _ts_labels() -> dict[str, str]:
    src = _ts_source()
    m = re.search(
        r"RELEASE_SECTION_LABELS[^=]*=\s*\{(.*?)\n\};", src, re.DOTALL
    )
    assert m, "could not find RELEASE_SECTION_LABELS in the mirror"
    pairs = re.findall(r'(?:"([^"]+)"|([A-Za-z_][\w]*))\s*:\s*"([^"]+)"', m.group(1))
    out = {(q or b): label for q, b, label in pairs}
    assert out, "parsed no labels out of the mirror"
    return out


def test_the_mirror_lists_exactly_the_python_sections_in_order():
    assert _ts_sections() == list(rn.SECTION_KEYS)


def test_the_mirror_labels_every_section_the_same_way():
    assert _ts_labels() == dict(rn.SECTION_LABELS)


def test_the_default_section_agrees():
    src = _ts_source()
    m = re.search(r'DEFAULT_RELEASE_SECTION\s*=\s*"([^"]+)"', src)
    assert m and m.group(1) == rn.DEFAULT_SECTION


def test_the_inherited_section_is_one_the_mirror_can_render():
    """`attach_release_inherited_cases` writes this key straight into the
    column; a value the web app has no label for would render as a slug."""
    assert rn.INHERITED_SECTION in _ts_sections()
