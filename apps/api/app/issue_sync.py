"""Best-effort GitHub issue open/close push for synced issues (US-1.20),
plus the payload builder for importing GitHub issues as `issues` rows
(US-2.1).

Called right after an issue's status flips to (or out of) a terminal state.
A failure here never fails the caller's status change — it's recorded as
an issue_events entry instead. Two call sites, two different auth contexts:
- review/dispatch routers hold the user's JWT, so they read/write through
  PostgREST (RLS-scoped).
- the runner callback holds no user JWT (shared-secret auth only), so it
  reads/writes through db.py's direct-Postgres helpers instead.

Naming note: "issue" here means our own `issues` row; a GitHub issue
payload is named `gh_issue` to avoid confusion between the two.
"""

from typing import Any

from . import db, github, github_tokens
from .config import Settings
from .supabase import postgrest_get, postgrest_post


def build_issue_row(*, org_id: str, project_id: str, gh_issue: dict) -> dict:
    """Build the `issues` insert payload for an imported GitHub issue.

    GitHub imports default to `bug` (US-2.2); they can be retyped while draft.
    """
    return {
        "org_id": org_id,
        "project_id": project_id,
        "title": gh_issue["title"],
        "body": gh_issue.get("body") or "",
        "status": "draft",
        "type": "bug",
        "github_issue_number": gh_issue["number"],
        "github_issue_url": gh_issue["html_url"],
    }


async def push_issue_state_via_postgrest(
    settings: Settings, user_token: str, issue_id: str, state: str
) -> None:
    # US-7.6: GitHub Issue sync is retired — requirements live in Build Mill
    # only. This push-back is now a no-op; call sites are left in place inert.
    return
    issues = await postgrest_get(  # noqa: unreachable — sync retired (us-7.6)
        settings,
        user_token,
        "issues",
        {
            "select": "org_id,project_id,github_issue_number",
            "id": f"eq.{issue_id}",
            "limit": "1",
        },
    )
    if not issues or not issues[0]["github_issue_number"]:
        return
    issue = issues[0]

    projects = await postgrest_get(
        settings,
        user_token,
        "projects",
        {
            "select": "repo_full_name,issue_sync_enabled",
            "id": f"eq.{issue['project_id']}",
            "limit": "1",
        },
    )
    if not projects or not projects[0]["issue_sync_enabled"]:
        return

    async def on_failure(message: str) -> None:
        await postgrest_post(
            settings,
            user_token,
            "issue_events",
            {
                "org_id": issue["org_id"],
                "issue_id": issue_id,
                "type": f"github-issue-{state}-failed",
                "payload": {"error": message},
            },
        )

    try:
        token = await github_tokens.token_for_user(
            settings, user_token, issue["org_id"], projects[0]["repo_full_name"]
        )
    except github.GitHubError as e:
        await on_failure(e.message)
        return

    await _apply_state(
        settings,
        owner_repo=projects[0]["repo_full_name"],
        token=token,
        issue_number=issue["github_issue_number"],
        state=state,
        on_failure=on_failure,
    )


async def push_issue_state_via_db(settings: Settings, issue_id: str, state: str) -> None:
    # US-7.6: GitHub Issue sync is retired — this push-back is now a no-op.
    return
    ctx = db.get_issue_sync_context(settings, issue_id)  # noqa: unreachable
    if not ctx:
        return

    async def on_failure(message: str) -> None:
        db.record_issue_event(
            settings,
            ctx["org_id"],
            issue_id,
            f"github-issue-{state}-failed",
            {"error": message},
        )

    try:
        token = await github_tokens.token_for_org(
            settings, str(ctx["org_id"]), ctx["repo_full_name"]
        )
    except github.GitHubError as e:
        await on_failure(e.message)
        return

    await _apply_state(
        settings,
        owner_repo=ctx["repo_full_name"],
        token=token,
        issue_number=ctx["github_issue_number"],
        state=state,
        on_failure=on_failure,
    )


async def _apply_state(
    settings: Settings,
    *,
    owner_repo: str,
    token: str,
    issue_number: int,
    state: str,
    on_failure: Any,
) -> None:
    owner, repo = owner_repo.split("/", 1)
    try:
        await github.set_issue_state(token, owner, repo, issue_number, state)
    except github.GitHubError as e:
        await on_failure(e.message)
