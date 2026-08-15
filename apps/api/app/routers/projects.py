"""GET /api/v1/projects/{id}/guidelines.md (US-1.18) and .../learnings.md
(US-1.21).

Both are assembled by one shared Postgres function each
(assemble_project_guidelines, assemble_project_learnings) so the FastAPI
endpoints and dispatch_issue's input_context always agree on the same
markdown.
"""

import asyncio
import re
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from .. import (
    db,
    github,
    github_tokens,
    releases,
    repo_docs,
    storage,
    wireframe_docs,
)
from ..auth import AuthUser, verify_token
from ..config import Settings, get_settings
from ..github import GitHubError
from ..supabase import RpcError, postgrest_get, postgrest_patch, postgrest_post, rpc

router = APIRouter(prefix="/projects", tags=["projects"])

# US-7.9: build-config keys are shell-safe env names, like deployment env vars.
BUILD_CONFIG_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class BuildConfigValueBody(BaseModel):
    value: str


async def _project_org_for_user(
    settings: Settings, token: str, project_id: str
) -> str:
    """The project's org_id, but only if the caller is a member (RLS). 404
    otherwise — no cross-org existence leak."""
    rows = await postgrest_get(
        settings,
        token,
        "projects",
        {"select": "org_id", "id": f"eq.{project_id}", "limit": "1"},
    )
    if not rows:
        raise HTTPException(status_code=404, detail="project not found")
    return rows[0]["org_id"]


@router.post("/{project_id}/docs-tree/sync")
async def docs_tree_sync(
    project_id: UUID,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-13.4: scaffold or rebuild the repo docs tree on demand — the
    first-use scaffold after enabling the flag, and the retry path after
    an approval-time write failure."""
    rows = await postgrest_get(
        settings,
        user.token,
        "projects",
        {
            "select": "id,docs_tree_enabled",
            "id": f"eq.{project_id}",
            "limit": "1",
        },
    )
    if not rows:
        raise HTTPException(status_code=404, detail="project not found")
    if not rows[0].get("docs_tree_enabled"):
        raise HTTPException(
            status_code=409,
            detail="the docs tree is not enabled for this project",
        )
    try:
        return await repo_docs.sync_tree(
            settings, str(project_id), trigger="manual sync"
        )
    except GitHubError as e:
        raise HTTPException(status_code=502, detail=f"GitHub: {e.message}")


@router.post("/{project_id}/wireframes/sync")
async def wireframes_sync(
    project_id: UUID,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-48.5: rebuild the whole wireframe tree from stored artifacts.

    Three things a per-hand-back write cannot do, which is why this exists:
    it cannot restyle existing wireframes when the kit changes, it cannot
    remove the file of a story that was abandoned or redrawn as "no UI
    surface", and it cannot produce an index. It is also the retry after a
    hand-back-time write failed — the artifact was stored either way.

    Unlike the docs tree there is no enable flag: a project either has
    wireframes or the sync writes an index saying it has none."""
    await _project_org_for_user(settings, user.token, str(project_id))
    try:
        return await wireframe_docs.sync_tree(
            settings, str(project_id), trigger="manual sync"
        )
    except GitHubError as e:
        raise HTTPException(status_code=502, detail=f"GitHub: {e.message}")


@router.put("/{project_id}/build-config/{name}")
async def set_build_config(
    project_id: UUID,
    name: str,
    body: BuildConfigValueBody,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-7.9: add or replace a build-config value. The value goes straight to
    the write-only data bucket and is never echoed back — the response and the
    listing surface only the NAME."""
    org_id = await _project_org_for_user(settings, user.token, str(project_id))
    if not BUILD_CONFIG_NAME_RE.match(name):
        raise HTTPException(
            status_code=400,
            detail="Names must be valid environment variable names"
            " (letters, digits, underscore; not starting with a digit).",
        )
    prefix = storage.build_config_prefix(org_id, str(project_id))
    await storage.put_object(
        settings, f"{prefix}/{name}", body.value.encode("utf-8")
    )
    await asyncio.to_thread(
        db.upsert_build_config_name,
        settings,
        org_id,
        str(project_id),
        name,
        user.email or "api",
    )
    return {"ok": True}


@router.delete("/{project_id}/build-config/{name}")
async def remove_build_config(
    project_id: UUID,
    name: str,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    org_id = await _project_org_for_user(settings, user.token, str(project_id))
    if not BUILD_CONFIG_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail="Invalid name")
    prefix = storage.build_config_prefix(org_id, str(project_id))
    await storage.delete_object(settings, f"{prefix}/{name}")
    await asyncio.to_thread(
        db.delete_build_config_name, settings, str(project_id), name
    )
    return {"ok": True}


@router.get("/{project_id}/guidelines.md", response_class=PlainTextResponse)
async def guidelines_md(
    project_id: UUID,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    try:
        markdown = await rpc(
            settings,
            user.token,
            "assemble_project_guidelines",
            {"p_project": str(project_id)},
        )
    except RpcError as e:
        if "not found" in e.message:
            raise HTTPException(status_code=404, detail=e.message)
        raise HTTPException(status_code=400, detail=e.message)

    return PlainTextResponse(content=markdown or "", media_type="text/markdown")


class GuidelinesRefreshBody(BaseModel):
    scope: str = "all"
    focus: str = ""


@router.post("/{project_id}/guidelines/refresh", status_code=202)
async def refresh_guidelines(
    project_id: UUID,
    body: GuidelinesRefreshBody,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-43.2: put an agent on writing this project's guidelines.

    US-43.6: this queues a PROJECT-SCOPED run — no work item, no chore, no
    stage rail. A refresh is not delivery work, and modelling it as a chore is
    what gave the manager a second, meaningless code-review gate for the same
    decision.

    The run is queued at queue_rank = -1 (US-43.5): a refresh must not sit
    behind the backlog it exists to correct.
    """
    # us-100.5: the run proposes WHOLE FILES — the Agent Instructions
    # document and per-task instruction files — keyed by file, accepted or
    # rejected whole. scope is 'all' (document + per-task files) or
    # 'document' (the Agent Instructions only).
    org_id = await _project_org_for_user(settings, user.token, str(project_id))

    def _dispatch():
        return db.dispatch_guidelines_refresh(
            settings,
            org_id,
            str(project_id),
            body.scope,
            body.focus,
        )

    try:
        result = await asyncio.to_thread(_dispatch)
    except ValueError as e:
        message = str(e)
        # An open refresh is a 409 that NAMES it, so the client can offer to
        # open the review instead of failing at the manager.
        if message.startswith("refresh-in-flight:"):
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "a guidelines refresh is already open on this "
                    "project — review it before starting another",
                    "refresh_id": message.split(":", 1)[1],
                },
            )
        raise HTTPException(status_code=400, detail=message)
    except Exception as e:  # noqa: BLE001 - the budget refusal is a db trigger
        # US-37.2 refuses the run insert from a BEFORE INSERT trigger. Its
        # message is written for a manager to read, so it is passed through
        # rather than replaced with a generic 500.
        text = str(e)
        if "budget" in text.lower():
            raise HTTPException(status_code=409, detail=text)
        raise

    return result


@router.get("/{project_id}/instructions/status")
async def instruction_publish_status(
    project_id: UUID,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-99.4: is anything edited but not yet published, and what?

    Publishing stopped being automatic (the dispatch-time write retired with
    us-99.4), so "edited but not in the repository" became a real state a
    manager can sit in without noticing. This is what makes it visible.

    The comparison is the same content hash migration 135 already defined —
    it survives edits that cancel out, reordering that changes nothing, and a
    failed write that must be retried, which no timestamp comparison does.
    """
    rows = await postgrest_get(
        settings,
        user.token,
        "projects",
        {
            "select": "id,repo_full_name,docs_tree_enabled,"
            "instructions_synced_hash,instructions_synced_at,"
            "instructions_synced_sha",
            "id": f"eq.{project_id}",
            "limit": "1",
        },
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Project not found")
    project = rows[0]

    guidelines = await rpc(
        settings, user.token, "assemble_project_guidelines",
        {"p_project": str(project_id)},
    )
    instructions = await asyncio.to_thread(
        db.get_project_instructions_for_publish, settings, str(project_id)
    )
    files, deletes = repo_docs.instruction_file_plan(
        instructions, guidelines, bool(project.get("docs_tree_enabled"))
    )
    digest = repo_docs.publish_hash(files, deletes)
    published = project.get("instructions_synced_hash") or ""

    return {
        "unpublished": digest != published,
        "has_repo": bool(project.get("repo_full_name")),
        # What a publish would write and remove — so the warning can say how
        # many files differ rather than only that something does.
        "files": sorted(files),
        "deletes": sorted(deletes),
        "hash": digest,
        "published_hash": published or None,
        "published_at": project.get("instructions_synced_at"),
        "published_sha": project.get("instructions_synced_sha"),
        # us-99.2 AC6 / us-99.4 AC6: true every time, so it is copy rather
        # than a dialog somebody dismisses once.
        "ownership_notice": (
            "Build Mill owns AGENTS.md, CLAUDE.md and everything under "
            ".buildmill/, and rewrites them whole on each publish."
        ),
    }


@router.get("/{project_id}/instructions/template-offers")
async def template_instruction_offers(
    project_id: UUID,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-99.7: what this project's template has changed since it was seeded.

    An offer, never a push. Each entry says which instruction differs and
    whether the project has ever edited it — `safe_to_accept` is true only
    when `updated_by` is null, meaning the seeding trigger wrote it and
    nobody has touched it since, so taking the update reverts nothing.

    RLS scopes the project read to the caller's orgs; a project they cannot
    see answers 404 rather than leaking that it exists.
    """
    rows = await postgrest_get(
        settings,
        user.token,
        "projects",
        {"select": "id,org_template_id", "id": f"eq.{project_id}", "limit": "1"},
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Project not found")
    if not rows[0].get("org_template_id"):
        return {"bound_to_template": False, "offers": []}

    offers = await asyncio.to_thread(
        db.get_template_instruction_offers, settings, str(project_id)
    )
    return {
        "bound_to_template": True,
        "offers": offers,
        "safe_count": sum(1 for o in offers if o["safe_to_accept"]),
        "conflicting_count": sum(1 for o in offers if not o["safe_to_accept"]),
    }


@router.post("/{project_id}/guidelines/save-instructions")
async def save_instructions(
    project_id: UUID,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """Write the factory-owned region of AGENTS.md and CLAUDE.md now
    (US-1.53), on the project's default branch.

    US-22.6: this used to replace AGENTS.md wholesale and stamp CLAUDE.md
    with a bare pointer, which destroyed the docs-tree section and any
    hand-written prose every time it was pressed. It now goes through the
    same assemble-and-merge path the docs sync uses, so pressing the button
    and approving a plan produce byte-identical files, and content outside
    the markers is never touched.

    The button stays because a manager who has just edited the guidelines may
    want them pushed *now* rather than at the next dispatch (US-22.7)."""
    projects = await postgrest_get(
        settings,
        user.token,
        "projects",
        {
            "select": "repo_full_name,default_branch,docs_tree_enabled,org_id",
            "id": f"eq.{project_id}",
            "limit": "1",
        },
    )
    if not projects:
        raise HTTPException(status_code=404, detail="project not found")
    project = projects[0]
    if "/" not in (project.get("repo_full_name") or ""):
        raise HTTPException(
            status_code=409, detail="This project has no linked repository."
        )
    branch = project["default_branch"]

    try:
        guidelines = await rpc(
            settings,
            user.token,
            "assemble_project_guidelines",
            {"p_project": str(project_id)},
        )
    except RpcError as e:
        raise HTTPException(status_code=400, detail=e.message) from e
    if not isinstance(guidelines, str) or not guidelines.strip():
        raise HTTPException(
            status_code=400,
            detail="Add at least one guideline section before saving instructions.",
        )

    try:
        token = await github_tokens.token_for_user(
            settings, user.token, project["org_id"], project["repo_full_name"]
        )
    except GitHubError as e:
        raise HTTPException(
            status_code=409, detail=f"GitHub not connected for this repo: {e.message}"
        )

    # us-99.2 AC5: ONE writer. Pressing Save and dispatching a run go through
    # the same pure planner, so they produce byte-identical files — the whole
    # point of the single-door rule US-22.6 introduced, now covering the
    # per-kind set as well as AGENTS.md.
    instructions = await asyncio.to_thread(
        db.get_project_instructions_for_publish, settings, str(project_id)
    )
    files, deletes = repo_docs.instruction_file_plan(
        instructions, guidelines, bool(project.get("docs_tree_enabled"))
    )
    try:
        # One commit carrying every file: either they all land or none does,
        # so the repo never holds a half-written instruction set.
        result = await repo_docs.commit_files(
            token,
            project["repo_full_name"],
            branch,
            "docs: build mill instructions (save)",
            files,
            deletes,
        )
    except GitHubError as e:
        raise HTTPException(status_code=502, detail=f"GitHub commit failed: {e.message}")

    commit_sha = result.get("commit_sha")
    if commit_sha and not result.get("unchanged"):
        db.record_instructions_sync(
            settings,
            str(project_id),
            repo_docs.publish_hash(files, deletes),
            commit_sha,
        )

    owner, repo = project["repo_full_name"].split("/", 1)
    return {
        "commit_sha": commit_sha,
        "unchanged": bool(result.get("unchanged")),
        "agents_md": {
            "html_url": f"https://github.com/{owner}/{repo}/blob/{branch}/AGENTS.md",
        },
        "claude_md": {
            "html_url": f"https://github.com/{owner}/{repo}/blob/{branch}/CLAUDE.md",
        },
    }


@router.get("/{project_id}/releases/preview")
async def preview_release(
    project_id: UUID,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-21.1: what cutting a release right now would produce — creating
    nothing. Returns the proposed version, the commit that would be pinned,
    the work items merged since the last released version, and `blockers`:
    the reasons a cut would be refused, so the dialog can say what to fix
    instead of failing on submit."""
    return await releases.build_preview(settings, user.token, str(project_id))


class CutReleaseBody(BaseModel):
    """US-21.1: the manager may override the proposed version. The AGENT never
    chooses one — it reads the version off the release row."""

    version: str | None = None


@router.post("/{project_id}/releases", status_code=201)
async def create_release(
    project_id: UUID,
    body: CutReleaseBody | None = None,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-21.1: cut a release from the default branch.

    Pins the branch head, snapshots the work items merged since the last
    released version, git-tags the pinned commit, and queues the release run.
    UAT is not a choice — every release goes there first, and Production is
    reached only by promotion (us-21.5).
    """
    preview = await releases.build_preview(settings, user.token, str(project_id))
    if preview["blockers"]:
        raise HTTPException(status_code=409, detail=preview["blockers"][0])

    version = ((body.version if body else None) or preview["version"] or "").strip()
    if not version:
        raise HTTPException(status_code=400, detail="Could not determine a version.")

    project = await releases.load_project(settings, user.token, str(project_id))
    prev = await releases.previous_release(settings, user.token, str(project_id))

    try:
        rows = await postgrest_post(
            settings,
            user.token,
            "releases",
            {
                "org_id": project["org_id"],
                "project_id": str(project_id),
                "version": version,
                "commit_sha": preview["commit_sha"],
                "previous_release_id": prev["id"] if prev else None,
                "included_items": preview["items"],
                "status": "queued",
            },
        )
    except Exception as e:
        msg = str(e)
        if "releases_one_in_flight_per_project" in msg:
            raise HTTPException(
                status_code=409,
                detail="A release is already in flight for this project.",
            )
        if "duplicate key" in msg or "releases_project_id_version_key" in msg:
            raise HTTPException(
                status_code=409, detail=f"Version {version} already exists."
            )
        if "row-level security" in msg or "42501" in msg:
            raise HTTPException(status_code=403, detail="Not a member of that organization.")
        raise HTTPException(status_code=400, detail="Could not cut the release.")

    release = rows[0]

    # Tag the pinned commit. A tagging failure must not lose the release row —
    # the release is the record, the tag is a convenience on top of it.
    tag_error: str | None = None
    # US-50.4: and cut release/<version> at the same commit, for every project.
    # A branch is the same act as the tag with a name the other system can
    # watch and a human can open a compare against; making it conditional on
    # having an external deployment would give two projects different release
    # artifacts for the same release model. Its failure discipline copies the
    # tag's exactly — reported, never fatal.
    branch_error: str | None = None
    release_branch = releases.release_branch_name(version)
    repo_full = project.get("repo_full_name") or ""
    gh_token = None
    owner = repo = ""
    if "/" in repo_full:
        owner, repo = repo_full.split("/", 1)
        try:
            gh_token = await github_tokens.token_for_user(
                settings, user.token, project["org_id"], repo_full
            )
            await github.create_tag(gh_token, owner, repo, version, release["commit_sha"])
            await postgrest_patch(
                settings,
                user.token,
                "releases",
                {"id": f"eq.{release['id']}"},
                {"git_tag": version},
            )
            release["git_tag"] = version
        except GitHubError as e:
            tag_error = e.message
        if gh_token:
            try:
                existing = await github.get_ref(
                    gh_token, owner, repo, release_branch
                )
                if not existing:
                    await github.create_ref(
                        gh_token,
                        owner,
                        repo,
                        release_branch,
                        release["commit_sha"],
                    )
            except GitHubError as e:
                branch_error = e.message
        else:
            branch_error = tag_error

    # US-82.3: record which declared modules this release touches, from the
    # real commit range — a suggestion engine for manual regression cases,
    # never a gate. Best-effort like the tag: no modules, no previous
    # release, or a compare failure just leaves the list empty.
    try:
        mods = await postgrest_get(
            settings,
            user.token,
            "project_modules",
            {"select": "name,path_globs", "project_id": f"eq.{project_id}"},
        )
        if mods and prev and gh_token and "/" in repo_full:
            import pathspec

            compare = await github.compare_commits(
                gh_token, owner, repo, prev["commit_sha"], release["commit_sha"]
            )
            changed = [
                str(f.get("filename") or "")
                for f in compare.get("files") or []
                if f.get("filename")
            ]
            touched = []
            for m in mods:
                globs = [str(g) for g in (m.get("path_globs") or []) if str(g).strip()]
                if not globs:
                    continue
                spec = pathspec.PathSpec.from_lines("gitwildmatch", globs)
                if any(spec.match_file(path) for path in changed):
                    touched.append(m["name"])
            if touched:
                await postgrest_patch(
                    settings,
                    user.token,
                    "releases",
                    {"id": f"eq.{release['id']}"},
                    {"touched_modules": touched},
                )
                release["touched_modules"] = touched
    except Exception:  # noqa: BLE001 — suggestions must never fail a cut
        pass

    # US-21.3: cutting a release queues the one agent job that takes it end to
    # end. A dispatch failure is reported, not fatal — the release exists and
    # can be re-dispatched once the reason is fixed (usually a missing or
    # agent-forbidden UAT deployment).
    dispatch = await asyncio.to_thread(
        db.dispatch_release_prep_for, settings, str(release["id"]), project["org_id"]
    )
    return {
        **release,
        "tag_error": tag_error,
        "release_branch": release_branch if not branch_error else None,
        "branch_error": branch_error,
        "run_id": dispatch.get("run_id"),
        "dispatch_error": dispatch.get("error"),
    }


@router.post("/{project_id}/releases/{release_id}/dispatch", status_code=202)
async def redispatch_release(
    project_id: UUID,
    release_id: UUID,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-21.3: queue (or re-queue) this release's agent job.

    Re-dispatch is the recovery path for a failed deployment. The run's
    context carries what the release has already reached, and the agent is
    told to resume — a retry does not rewrite notes that are already stored.
    """
    org_id = await _project_org_for_user(settings, user.token, str(project_id))
    result = await asyncio.to_thread(
        db.dispatch_release_prep_for, settings, str(release_id), org_id
    )
    if "error" in result:
        raise HTTPException(status_code=409, detail=result["error"])
    return {"ok": True, **result}


@router.get("/{project_id}/learnings.md", response_class=PlainTextResponse)
async def learnings_md(
    project_id: UUID,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    try:
        markdown = await rpc(
            settings,
            user.token,
            "assemble_project_learnings",
            {"p_project": str(project_id)},
        )
    except RpcError as e:
        if "not found" in e.message:
            raise HTTPException(status_code=404, detail=e.message)
        raise HTTPException(status_code=400, detail=e.message)

    return PlainTextResponse(content=markdown or "", media_type="text/markdown")


# ---------------------------------------------------------------------------
# US-89.2: the project environment — secret values are WRITE-ONLY
# ---------------------------------------------------------------------------
# Rows are plain CRUD under RLS from the browser; these two endpoints exist
# only for what RLS cannot do: putting a secret VALUE where the browser can
# never read it back (the private data bucket, us-1.28 pattern), and removing
# a secret entry together with its object.


async def _require_manage_work_on(
    settings: Settings, user: AuthUser, org_id: str
) -> None:
    try:
        ok = await rpc(
            settings,
            user.token,
            "has_org_capability",
            {"p_org": org_id, "p_capability": "manage_work"},
        )
    except RpcError:
        ok = False
    if not ok:
        raise HTTPException(
            status_code=403, detail="Not authorized to manage the environment"
        )


async def _get_env_entry_for_user(
    settings: Settings, user: AuthUser, project_id: str, entry_id: str
) -> dict:
    # Fetched under the caller's own JWT: RLS is the org isolation.
    rows = await postgrest_get(
        settings,
        user.token,
        "project_env",
        {
            "select": "*",
            "id": f"eq.{entry_id}",
            "project_id": f"eq.{project_id}",
            "limit": "1",
        },
    )
    if not rows:
        raise HTTPException(status_code=404, detail="environment entry not found")
    return rows[0]


class EnvSecretBody(BaseModel):
    value: str


@router.post("/{project_id}/env/{entry_id}/secret")
async def set_env_secret(
    project_id: UUID,
    entry_id: UUID,
    body: EnvSecretBody,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """Store a secret entry's value. Entered once, never readable back —
    the UI shows `Set · <fingerprint>` from the row afterwards."""
    from .. import project_env as project_env_lib
    from .. import storage

    if not body.value or not body.value.strip():
        raise HTTPException(status_code=422, detail="value is required")
    entry = await _get_env_entry_for_user(
        settings, user, str(project_id), str(entry_id)
    )
    if entry["kind"] != "secret":
        raise HTTPException(
            status_code=409,
            detail="this entry is plain — edit its value directly",
        )
    await _require_manage_work_on(settings, user, str(entry["org_id"]))
    await storage.put_object(
        settings,
        project_env_lib.secret_path(
            str(entry["org_id"]), str(project_id), str(entry_id)
        ),
        body.value.encode("utf-8"),
        "text/plain",
    )
    fp = project_env_lib.fingerprint(body.value)
    await asyncio.to_thread(
        project_env_lib.mark_secret_set, settings, str(entry_id), fp, user.id
    )
    return {"set": True, "fingerprint": fp}


@router.delete("/{project_id}/env/{entry_id}")
async def delete_env_entry(
    project_id: UUID,
    entry_id: UUID,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """Remove an entry — and, for a secret, its stored value with it."""
    from .. import project_env as project_env_lib
    from .. import storage

    entry = await _get_env_entry_for_user(
        settings, user, str(project_id), str(entry_id)
    )
    await _require_manage_work_on(settings, user, str(entry["org_id"]))
    if entry["kind"] == "secret":
        await storage.delete_object(
            settings,
            project_env_lib.secret_path(
                str(entry["org_id"]), str(project_id), str(entry_id)
            ),
        )
    await asyncio.to_thread(
        project_env_lib.delete_entry, settings, str(entry_id)
    )
    return {"deleted": True}
