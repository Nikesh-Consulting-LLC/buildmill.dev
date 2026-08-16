"""US-1.17: diff parsing into change metrics."""

from app.metrics import classify_area, compute_diff_metrics, is_vendored


def _diff(*paths_and_adds: tuple[str, int]) -> str:
    """A minimal unified diff adding N lines to each path."""
    out = []
    for path, n in paths_and_adds:
        out.append(f"diff --git a/{path} b/{path}\n")
        out.append("--- /dev/null\n")
        out.append(f"+++ b/{path}\n")
        out.append(f"@@ -0,0 +1,{n} @@\n")
        out.extend(f"+line {i}\n" for i in range(n))
    return "".join(out)


def test_no_diff_returns_none():
    assert compute_diff_metrics(None) is None
    assert compute_diff_metrics("") is None
    assert compute_diff_metrics("   ") is None


def test_single_file_counts_added_and_removed_lines():
    diff = (
        "diff --git a/apps/api/app/main.py b/apps/api/app/main.py\n"
        "--- a/apps/api/app/main.py\n"
        "+++ b/apps/api/app/main.py\n"
        "@@ -1,2 +1,3 @@\n"
        "+new line\n"
        "-old line\n"
        " unchanged\n"
    )
    result = compute_diff_metrics(diff)
    assert result["lines_added"] == 1
    assert result["lines_removed"] == 1
    assert result["files_changed"] == 1
    assert result["change_breakdown"] == [
        {"path": "apps/api/app/main.py", "added": 1, "removed": 1, "area": "backend"}
    ]


def test_multiple_hunks_for_same_path_are_merged():
    """A retry (US-1.13) appends a second diff --git block for the same file."""
    diff = (
        "diff --git a/src/health.py b/src/health.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/src/health.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+line one\n"
        "+line two\n"
        "diff --git a/src/health.py b/src/health.py\n"
        "--- a/src/health.py\n"
        "+++ b/src/health.py\n"
        "@@ -1,2 +1,3 @@\n"
        "+line three\n"
    )
    result = compute_diff_metrics(diff)
    assert result["files_changed"] == 1
    assert result["lines_added"] == 3
    assert result["lines_removed"] == 0


def test_frontend_backend_other_split():
    diff = (
        "diff --git a/apps/web/src/app/page.tsx b/apps/web/src/app/page.tsx\n"
        "--- a/apps/web/src/app/page.tsx\n"
        "+++ b/apps/web/src/app/page.tsx\n"
        "@@ -1,1 +1,2 @@\n"
        "+frontend line\n"
        "diff --git a/infra/supabase/migrations/011_x.sql b/infra/supabase/migrations/011_x.sql\n"
        "--- /dev/null\n"
        "+++ b/infra/supabase/migrations/011_x.sql\n"
        "@@ -0,0 +1,1 @@\n"
        "+backend line\n"
        "diff --git a/README.md b/README.md\n"
        "--- a/README.md\n"
        "+++ b/README.md\n"
        "@@ -1,1 +1,2 @@\n"
        "+doc line\n"
    )
    result = compute_diff_metrics(diff)
    areas = {f["path"]: f["area"] for f in result["change_breakdown"]}
    assert areas["apps/web/src/app/page.tsx"] == "frontend"
    assert areas["infra/supabase/migrations/011_x.sql"] == "backend"
    assert areas["README.md"] == "other"
    assert result["files_changed"] == 3
    assert result["lines_added"] == 3


def test_classify_area_heuristic():
    assert classify_area("apps/web/src/components/foo.tsx") == "frontend"
    assert classify_area("src/styles/app.css") == "frontend"
    assert classify_area("apps/api/app/main.py") == "backend"
    assert classify_area("infra/supabase/migrations/001_initial.sql") == "backend"
    assert classify_area("README.md") == "other"


# --------------------------------------------------------------- us-109.3


def test_vendored_paths_are_recognised():
    assert is_vendored("node_modules/react/index.js")
    assert is_vendored("apps/web/node_modules/left-pad/index.js")
    assert is_vendored("apps/api/.venv/lib/site-packages/httpx/_api.py")
    assert is_vendored("apps/web/.next/static/chunks/main.js")
    assert is_vendored("package-lock.json")
    assert is_vendored("apps/web/pnpm-lock.yaml")
    assert is_vendored("public/js/jquery.min.js")
    assert is_vendored("dist/bundle.js.map")
    assert is_vendored("target/debug/build/foo.rs")


def test_authored_paths_that_merely_look_vendored_are_not():
    """The substring trap: silently discounting real work is worse than the
    bug being fixed, so matching is on whole path segments."""
    assert not is_vendored("apps/web/src/redistribute/index.ts")
    assert not is_vendored("apps/api/app/buildings.py")
    assert not is_vendored("src/vendors/stripe-client.ts")
    assert not is_vendored("apps/web/src/lib/distance.ts")
    assert not is_vendored("package.json")
    assert not is_vendored("apps/web/src/components/target-picker.tsx")
    # A file named for a vendored DIRECTORY is still an authored file.
    assert not is_vendored("docs/node_modules.md")


def test_vendored_files_do_not_count_as_output():
    """The 2026-08-09 shape: a handful of authored files beside a dependency
    tree. Only the authored ones are anybody's work."""
    diff = _diff(
        ("apps/web/src/app/page.tsx", 10),
        ("apps/api/app/main.py", 5),
        ("node_modules/react/index.js", 5000),
        ("package-lock.json", 900),
    )
    result = compute_diff_metrics(diff)
    assert result["lines_added"] == 15
    assert result["files_changed"] == 2
    # ...but the changeset really did carry them, so the record still says so.
    assert len(result["change_breakdown"]) == 4
    areas = {f["path"]: f["area"] for f in result["change_breakdown"]}
    assert areas["node_modules/react/index.js"] == "vendored"
    assert areas["package-lock.json"] == "vendored"
    assert areas["apps/web/src/app/page.tsx"] == "frontend"


def test_vendored_beats_a_matching_frontend_marker():
    """`apps/web/node_modules/...` matches the frontend marker too. Whichever
    check wins decides whether 1.8M lines count as somebody's work."""
    assert classify_area("apps/web/node_modules/react/index.js") == "vendored"
    assert classify_area("apps/web/.next/static/main.css") == "vendored"


def test_an_entirely_vendored_changeset_counts_as_zero_not_none():
    """None means "no diff to parse" and leaves the columns null. A changeset
    that is all dependency tree was parsed fine and produced no output — those
    are different facts and must not collapse."""
    result = compute_diff_metrics(_diff(("node_modules/react/index.js", 5000)))
    assert result is not None
    assert result["lines_added"] == 0
    assert result["files_changed"] == 0
    assert len(result["change_breakdown"]) == 1
