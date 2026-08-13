"""US-7.6: GitHub Issue sync is retired — the pull endpoint returns 410 and
the push-back is a no-op. (Was US-1.20 / US-2.1.)"""

import uuid

PROJECT_ID = str(uuid.uuid4())


def _auth(make_token):
    return {"Authorization": f"Bearer {make_token()}"}


def test_pull_issues_is_retired_410(client, make_token):
    resp = client.post(
        f"/api/v1/github/projects/{PROJECT_ID}/issues/pull", headers=_auth(make_token)
    )
    assert resp.status_code == 410
    assert "retired" in resp.json()["detail"].lower()


def test_pull_issues_without_token_is_401(client):
    resp = client.post(f"/api/v1/github/projects/{PROJECT_ID}/issues/pull")
    assert resp.status_code == 401


def test_build_issue_row_still_importable(client):
    """build_issue_row remains importable (github_issue_number stays an inert
    column); imported issues keep their content — no data loss."""
    from app import issue_sync

    row = issue_sync.build_issue_row(
        org_id="org-1",
        project_id="proj-1",
        gh_issue={
            "number": 7,
            "title": "Broken CSV export",
            "body": "It 500s",
            "html_url": "https://github.com/acme/webshop/issues/7",
        },
    )
    assert row["type"] == "bug"
    assert row["github_issue_number"] == 7
    assert row["title"] == "Broken CSV export"


def test_push_issue_state_via_db_is_noop(client):
    """US-7.6: the runner-side push-back does nothing now."""
    import asyncio

    from app import issue_sync
    from app.config import Settings

    settings = Settings(
        supabase_url="https://test.supabase.co",
        supabase_publishable_key="sb_publishable_test",
        cors_origins="http://localhost:3000",
        database_url="postgresql://test",
    )
    # Returns without touching the DB / GitHub — no exception despite the
    # unreachable DB url.
    asyncio.run(
        issue_sync.push_issue_state_via_db(settings, str(uuid.uuid4()), "closed")
    )
