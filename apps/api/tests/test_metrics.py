"""US-1.17: diff parsing into change metrics."""

from app.metrics import classify_area, compute_diff_metrics


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
