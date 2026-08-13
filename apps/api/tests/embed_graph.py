"""PostgREST's relationship graph, and every embed this repo asks it to walk.

BUG-1.1: `app_issues` (US-16.1) gave PostgREST a second way to get from
`deployments` to `projects` — the direct foreign key, and the junction path it
infers from a table holding NOT NULL foreign keys to both. Faced with two, it
refuses: `300 Multiple Choices`, code `PGRST201`. Every un-hinted
`projects(...)` embed on a deployment started failing at once, which is how a
delete button came to answer 500.

The failure is structural, so the guard is too. Both halves are derived, never
listed by hand:

* the graph, from the checked-in generated types (and, where a database is
  reachable, from `pg_catalog` — see the `_sql` module);
* the embeds, by reading the selects in `apps/api` and `apps/web`.

A migration that makes an embed the app already relies on ambiguous then fails
a test instead of a manager's click.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
GENERATED_TYPES = REPO_ROOT / "apps/web/src/lib/supabase/database.types.ts"


@dataclass(frozen=True)
class ForeignKey:
    """One FK constraint, plus whether every one of its columns is NOT NULL —
    the property that decides whether PostgREST will treat the table holding
    it as one side of a junction."""

    table: str
    name: str
    columns: tuple[str, ...]
    ref_table: str
    not_null: bool


@dataclass(frozen=True)
class Embed:
    """`child` embedded into a select on `parent`, with the `!constraint` hint
    if the author named one."""

    parent: str
    child: str
    hint: str | None
    where: str


@dataclass(frozen=True)
class Path:
    """One way PostgREST can get from a table to another."""

    kind: str  # "fk" | "m2m"
    via: str  # the table holding the constraint(s)
    name: str  # constraint name, or the two junction constraints

    def satisfies(self, hint: str) -> bool:
        """PostgREST accepts either a constraint name or the junction table."""
        return hint == self.via or hint in self.name.split("+")


# --------------------------------------------------------------- the graph


def parse_generated_types(text: str) -> list[ForeignKey]:
    """Read the FK graph out of `database.types.ts`.

    The generated file carries every constraint (`Relationships`) and every
    column's nullability (`Row`), which is exactly what the junction rule
    needs — so the guard runs with no database in reach.
    """
    lines = text.splitlines()
    try:
        start = lines.index("    Tables: {")
        end = lines.index("    Views: {")
    except ValueError as e:  # pragma: no cover - regenerated file changed shape
        raise AssertionError(f"unrecognised {GENERATED_TYPES.name} layout: {e}")

    fks: list[ForeignKey] = []
    table: str | None = None
    not_null: set[str] = set()
    section: str | None = None
    fk: dict[str, object] = {}
    for line in lines[start + 1 : end]:
        if m := re.fullmatch(r"      (\w+): \{", line):
            table, not_null, section, fk = m.group(1), set(), None, {}
            continue
        if table is None:
            continue
        if line == "        Row: {":
            section = "row"
        elif line in ("        Insert: {", "        Update: {"):
            section = None
        elif line == "        Relationships: [":
            section = "rel"
        elif section == "row":
            if m := re.fullmatch(r"          (\w+): (.+)", line):
                if "null" not in m.group(2):
                    not_null.add(m.group(1))
        elif section == "rel":
            if m := re.fullmatch(r'            foreignKeyName: "(.+)"', line):
                fk["name"] = m.group(1)
            elif m := re.fullmatch(r"            columns: \[(.*)\]", line):
                fk["columns"] = tuple(
                    c.strip().strip('"') for c in m.group(1).split(",") if c.strip()
                )
            elif m := re.fullmatch(r'            referencedRelation: "(.+)"', line):
                fk["ref_table"] = m.group(1)
            elif line == "          }," and fk.get("name"):
                columns = fk.get("columns", ())
                fks.append(
                    ForeignKey(
                        table=table,
                        name=str(fk["name"]),
                        columns=columns,  # type: ignore[arg-type]
                        ref_table=str(fk["ref_table"]),
                        not_null=all(c in not_null for c in columns),
                    )
                )
                fk = {}
    return fks


def relationship_paths(fks: list[ForeignKey]) -> dict[frozenset[str], list[Path]]:
    """Every route PostgREST can take between two tables.

    Two kinds: a foreign key either way, and a junction — a table holding two
    NOT NULL foreign keys to two different tables, which PostgREST reads as a
    many-to-many between them. The NOT NULL part is load-bearing: an optional
    link (`documents.issue_id`) is not a junction, which is why embedding
    `projects` on an `issue` still resolves.
    """
    paths: dict[frozenset[str], list[Path]] = {}

    def add(a: str, b: str, path: Path) -> None:
        if a != b:
            paths.setdefault(frozenset((a, b)), []).append(path)

    by_table: dict[str, list[ForeignKey]] = {}
    for fk in fks:
        add(fk.table, fk.ref_table, Path("fk", fk.table, fk.name))
        by_table.setdefault(fk.table, []).append(fk)

    for junction, held in by_table.items():
        linking = [fk for fk in held if fk.not_null]
        for i, left in enumerate(linking):
            for right in linking[i + 1 :]:
                if junction in (left.ref_table, right.ref_table):
                    continue
                add(
                    left.ref_table,
                    right.ref_table,
                    Path("m2m", junction, f"{left.name}+{right.name}"),
                )
    return paths


def ambiguous_pairs(fks: list[ForeignKey]) -> dict[frozenset[str], list[Path]]:
    """The pairs PostgREST will refuse to embed without being told which way."""
    return {k: v for k, v in relationship_paths(fks).items() if len(v) > 1}


# --------------------------------------------------------------- the embeds


_LITERAL = r'(?:"(?:[^"\\]|\\.)*"\s*\+?\s*)+'  # one string, or several concatenated
_TS_QUERY = re.compile(
    r'\.from\("(?P<table>\w+)"\)(?P<gap>.{0,800}?)\.select\(\s*(?P<sel>' + _LITERAL + r")",
    re.S,
)
_PY_SELECT = re.compile(r'"select":\s*\(?\s*(?P<sel>' + _LITERAL + r")", re.S)
_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')


def _literal(fragment: str) -> str | None:
    """Join an implicitly concatenated string literal; None if it is not one
    (a template literal or a variable — nothing we can read statically)."""
    if "`" in fragment or "${" in fragment:
        return None
    pieces = _STRING.findall(fragment)
    return "".join(pieces) if pieces else None


def parse_select(select: str, parent: str, where: str) -> list[Embed]:
    """Split a PostgREST select into the embeds it walks.

    Nesting counts: in `runs?select=issues!fk(projects(name))` the inner embed
    hangs off `issues`, not `runs`, and checking it against the wrong parent
    would flag a relationship that was never asked for.
    """
    embeds: list[Embed] = []
    depth, start = 0, 0
    items: list[str] = []
    for i, ch in enumerate(select):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            items.append(select[start:i])
            start = i + 1
    items.append(select[start:])

    for item in items:
        item = item.strip()
        if "(" not in item or not item.endswith(")"):
            continue
        head, inner = item.split("(", 1)
        inner = inner[:-1]
        head = head.split(":")[-1].strip().lstrip(".")  # drop `alias:` and `...spread`
        name, *modifiers = head.split("!")
        hint = next((m for m in modifiers if m not in ("inner", "left")), None)
        if not re.fullmatch(r"\w+", name):
            continue
        embeds.append(Embed(parent=parent, child=name, hint=hint, where=where))
        embeds.extend(parse_select(inner, name, where))
    return embeds


def _web_embeds(root: Path, tables: set[str]) -> list[Embed]:
    found: list[Embed] = []
    for path in sorted(root.rglob("*.ts")) + sorted(root.rglob("*.tsx")):
        text = path.read_text(encoding="utf-8")
        for m in _TS_QUERY.finditer(text):
            parent = m.group("table")
            if '.from("' in m.group("gap") or parent not in tables:
                continue  # the select belongs to a later query, not this one
            select = _literal(m.group("sel"))
            if select:
                found.extend(
                    parse_select(select, parent, path.relative_to(REPO_ROOT).as_posix())
                )
    return found


def _api_embeds(root: Path, tables: set[str]) -> list[Embed]:
    """The API states its table as a positional argument
    (`postgrest_get(settings, token, "deployments", {"select": ...})`), so the
    nearest table name quoted before the select is the one being read."""
    found: list[Embed] = []
    for path in sorted(root.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for m in _PY_SELECT.finditer(text):
            select = _literal(m.group("sel"))
            if not select:
                continue
            preceding = [s for s in _STRING.findall(text[: m.start()]) if s in tables]
            if not preceding:
                continue
            found.extend(
                parse_select(
                    select, preceding[-1], path.relative_to(REPO_ROOT).as_posix()
                )
            )
    return found


def find_embeds(tables: set[str]) -> list[Embed]:
    """Every cross-table embed in the repo, best effort.

    A select built at runtime is invisible here — the guard is a floor, not a
    proof, which is why `test_embed_ambiguity` also pins the count it expects
    to keep finding.
    """
    embeds = _web_embeds(REPO_ROOT / "apps/web/src", tables) + _api_embeds(
        REPO_ROOT / "apps/api/app", tables
    )
    return [e for e in embeds if e.child in tables and e.child != e.parent]


# --------------------------------------------------------------- the verdict


def violations(fks: list[ForeignKey], embeds: list[Embed]) -> list[str]:
    """Embeds PostgREST would refuse, or hints that name the wrong thing."""
    paths = relationship_paths(fks)
    problems: list[str] = []
    for embed in embeds:
        routes = paths.get(frozenset((embed.parent, embed.child)), [])
        if embed.hint is None:
            if len(routes) > 1:
                problems.append(
                    f"{embed.where}: {embed.parent} -> {embed.child}({...}) is"
                    f" ambiguous ({len(routes)} relationships:"
                    f" {', '.join(sorted(r.name for r in routes))}) and needs"
                    f" a !constraint hint"
                )
        elif not any(route.satisfies(embed.hint) for route in routes):
            problems.append(
                f"{embed.where}: {embed.parent} -> {embed.child}!{embed.hint}"
                f" names no relationship between them"
                f" (have: {', '.join(sorted(r.name for r in routes)) or 'none'})"
            )
    return problems
