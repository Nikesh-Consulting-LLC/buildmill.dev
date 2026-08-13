"""BUG-1.1: no embed this app relies on may be one migration from a 300.

The graph comes from the checked-in generated types, the embeds from the
selects in `apps/api` and `apps/web` — see `embed_graph`. Neither side is a
list somebody has to remember to update, which is the point: the next table
that turns a working embed ambiguous fails here instead of in production.

`test_embed_ambiguity_sql` runs the same check against the live relationship
graph where a database is reachable.
"""

import embed_graph as eg
import pytest


@pytest.fixture(scope="module")
def fks():
    return eg.parse_generated_types(eg.GENERATED_TYPES.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def tables(fks):
    return {fk.table for fk in fks} | {fk.ref_table for fk in fks}


@pytest.fixture(scope="module")
def embeds(tables):
    return eg.find_embeds(tables)


def test_the_generated_types_still_parse(fks, tables):
    """A regenerated types file that changed shape would leave the parser
    finding nothing and every check below passing on an empty graph."""
    assert len(tables) > 50
    assert len(fks) > 100
    assert any(fk.name == "deployments_project_id_org_id_fkey" for fk in fks)


def test_the_scan_still_finds_the_embeds_it_guards(embeds):
    """Same failure mode from the other side: a select written some new way
    the scanner cannot read would quietly shrink what is covered."""
    assert len(embeds) > 50
    pairs = {(e.parent, e.child) for e in embeds}
    for pair in [
        ("deployments", "projects"),  # the pre-flight read and the server pages
        ("runs", "issues"),
        ("issues", "projects"),
        ("epics", "projects"),
    ]:
        assert pair in pairs, pair


def test_no_embed_the_app_relies_on_is_ambiguous(fks, embeds):
    """The regression itself. Every violation names its file, its pair, and
    the relationships PostgREST would be choosing between."""
    assert eg.violations(fks, embeds) == []


def test_the_junction_behind_this_bug_is_visible_in_the_graph(fks):
    """`app_issues` holds NOT NULL foreign keys to both sides, so PostgREST
    reads it as a many-to-many and `deployments -> projects` gains a second
    route. Pinned because the hints in the code only make sense with it."""
    routes = eg.relationship_paths(fks)[frozenset(("deployments", "projects"))]
    assert len(routes) > 1
    assert any(r.kind == "m2m" and r.via == "app_issues" for r in routes)
    assert any(r.name == "deployments_project_id_org_id_fkey" for r in routes)


def test_an_optional_link_is_not_a_junction(fks):
    """The guard has to stay quiet where PostgREST does. `documents` points
    at both `issues` and `projects`, but its `issue_id` is nullable — so it
    is no junction, `issues -> projects` still resolves on its own, and the
    work item page's un-hinted embed is correct as written."""
    assert frozenset(("issues", "projects")) not in eg.ambiguous_pairs(fks)


# --- the guard's own failure modes -------------------------------------------


def _fk(table, name, ref, columns=("x",), not_null=True):
    return eg.ForeignKey(table, name, columns, ref, not_null)


AMBIGUOUS_SCHEMA = [
    _fk("deployments", "deployments_project_fk", "projects"),
    _fk("app_issues", "app_issues_deployment_fk", "deployments"),
    _fk("app_issues", "app_issues_project_fk", "projects"),
]


def test_an_unhinted_ambiguous_embed_is_reported():
    embed = eg.Embed("deployments", "projects", None, "somewhere.py")
    problems = eg.violations(AMBIGUOUS_SCHEMA, [embed])
    assert len(problems) == 1
    assert "needs a !constraint hint" in problems[0]


def test_a_hint_naming_the_wrong_relationship_is_reported():
    """A hint that points somewhere else does not error at runtime — it
    returns different rows — so it has to be caught here."""
    embed = eg.Embed("deployments", "projects", "servers_org_id_fkey", "somewhere.py")
    problems = eg.violations(AMBIGUOUS_SCHEMA, [embed])
    assert len(problems) == 1
    assert "names no relationship" in problems[0]


def test_a_correct_hint_passes():
    for hint in ("deployments_project_fk", "app_issues"):
        embed = eg.Embed("deployments", "projects", hint, "somewhere.py")
        assert eg.violations(AMBIGUOUS_SCHEMA, [embed]) == []


def test_a_nullable_link_does_not_make_a_junction():
    schema = [
        _fk("documents", "documents_issue_fk", "issues", not_null=False),
        _fk("documents", "documents_project_fk", "projects"),
        _fk("issues", "issues_project_fk", "projects"),
    ]
    assert eg.violations(schema, [eg.Embed("issues", "projects", None, "x")]) == []


def test_a_nested_embed_is_checked_against_its_own_parent():
    """`runs?select=issues!fk(projects(name))` embeds projects on ISSUES."""
    parsed = eg.parse_select("id, issues!runs_issue_fk(title, projects(name))", "runs", "x")
    assert (
        eg.Embed("runs", "issues", "runs_issue_fk", "x") in parsed
        and eg.Embed("issues", "projects", None, "x") in parsed
    )


def test_modifiers_and_aliases_do_not_hide_the_hint():
    parsed = eg.parse_select("project:projects!deployments_project_fk!inner(name)", "deployments", "x")
    assert parsed == [eg.Embed("deployments", "projects", "deployments_project_fk", "x")]
