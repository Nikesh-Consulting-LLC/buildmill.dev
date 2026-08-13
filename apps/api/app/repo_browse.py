"""Read-only repository access over MCP (US-5.20) and the shared ref
contract: a run reads from its work branch when that exists on GitHub
(retries, in-progress code runs), otherwise the repo's default branch.
US-5.25's workspace snapshot pins its archive to the same resolution.
"""

from typing import Any

from . import github

# Tree responses are capped by entry count with an explicit truncation
# note; file reads by byte size with an explicit error — never silent loss.
MAX_TREE_ENTRIES = 500
MAX_FILE_BYTES = 200_000


def work_branch(issue_id: str) -> str:
    return f"factory/issue-{issue_id}"


async def resolve_ref(
    token: str,
    ic: dict[str, Any],
    issue_id: str,
    explicit_ref: str = "",
    run_branch: str = "",
) -> str:
    """US-5.20 ref defaulting. An explicit ref always wins (any branch or
    sha in the same repo — GitHub answers not-found for the rest). US-7.3:
    the run's strategy-resolved working branch is tried first when known,
    then the legacy factory/issue-<id> name, then the default branch."""
    if explicit_ref.strip():
        return explicit_ref.strip()
    owner, repo = (ic.get("repo_full_name") or "/").split("/", 1)
    candidates = [b for b in (run_branch.strip(), work_branch(issue_id)) if b]
    for branch in candidates:
        try:
            await github.get_branch(token, owner, repo, branch)
            return branch
        except github.GitHubCredentialError:
            raise
        except github.GitHubError:
            continue
    return ic.get("default_branch") or "main"
