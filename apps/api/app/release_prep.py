"""US-63.1/63.2/63.3: release prep's shared claim/submit contract.

The whole point of this module existing separately from factory_mcp.py or
worker.py: both the MCP tool and the plain-HTTP worker endpoint call the SAME
functions here, so neither transport can silently complete a release prep
job past requirements the other one enforces. That gap — a git-native worker
closing a release run out through a generic submit endpoint that had no idea
notes/deploy were still required — is exactly what stranded the first live
release run on 2026-08-01.

Deploying to UAT is not judgment work an agent does — it is deploy.py's own
deterministic pipeline, fired directly from submit() the moment notes land.
"""

from __future__ import annotations

from typing import Any

from . import db, deploy, documents
from .config import Settings


async def claim(
    settings: Settings, prep_id: str, worker: dict[str, Any]
) -> dict[str, Any]:
    row = db.claim_release_prep(settings, prep_id, worker)
    if not row:
        return {"error": "not available to claim — already claimed or not queued"}
    return {"ok": True, "id": prep_id, "release_id": str(row["release_id"])}


async def submit(
    settings: Settings,
    prep_id: str,
    worker: dict[str, Any],
    notes_summary: str,
    notes_detail: str,
    test_cases: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    prep = db.get_release_prep(settings, prep_id, str(worker["org_id"]))
    if not prep:
        return {"error": "release prep not found"}
    if str(prep.get("worker_id") or "") != str(worker["id"]):
        return {"error": "you do not hold this release prep"}
    if prep["status"] != "running":
        return {"error": f"release prep is {prep['status']}, not running"}

    release = db.get_release(settings, str(prep["release_id"]))
    if not release:
        db.fail_release_prep(settings, prep_id, "the linked release no longer exists")
        return {"error": "the linked release no longer exists"}

    if not (notes_summary or "").strip():
        return {"error": "notes_summary is required — a few lines a manager reads at a glance"}
    if not (notes_detail or "").strip():
        return {
            "error": "notes_detail is required — what actually changed: schema, "
            "migrations, modules"
        }

    # US-7.14's rule: the manager fixed the version when they cut the
    # release; the agent only ever reads it.
    version = str(release["version"])
    first_line = next((ln for ln in notes_summary.splitlines() if ln.strip()), "")
    if version not in first_line:
        return {
            "error": f"the summary's title must carry the version {version} "
            f"(got: {first_line[:80]!r})"
        }

    # US-21.4: the release's test-case set is ASSEMBLED, not invented — every
    # included work item's active cases come across first, then whatever the
    # agent authored sits alongside them.
    inherited = db.attach_release_inherited_cases(settings, str(release["id"]))
    attached = 0
    if test_cases:
        attached = db.attach_release_test_cases(
            settings,
            release_id=str(release["id"]),
            org_id=str(worker["org_id"]),
            project_id=str(prep["project_id"]),
            cases=test_cases,
        )

    db.update_release(
        settings,
        str(release["id"]),
        {
            "notes_summary": notes_summary,
            "notes_detail": notes_detail,
            "status": "notes-ready",
        },
    )
    db.stamp_release_milestones(
        settings,
        str(release["id"]),
        notes_written=True,
        cases_attached=bool(attached or inherited),
    )

    doc_id = ""
    try:
        doc = await documents.create_or_replace(
            settings,
            org_id=str(worker["org_id"]),
            project_id=str(prep["project_id"]),
            name=f"release-notes-{version.lower()}.md",
            content="\n\n".join(
                [f"# Release {version}", notes_summary.strip(), notes_detail.strip()]
            ).encode(),
            source="agent",
            attached_to="project",
            mime_type="text/markdown",
        )
        doc_id = str(doc.get("id") or "")
    except Exception:
        # A failed document write must not fail a release whose notes and
        # test cases are already recorded on the release row.
        doc_id = ""

    db.complete_release_prep(settings, prep_id, "succeeded")

    # US-63.2: fire the UAT deploy immediately — no agent, no manager click.
    # A failure here is not this function's failure: notes/cases are already
    # committed, and the release lands at 'uat-deploy-failed' (still
    # in-flight, per migration 215) rather than success masking a dead end.
    deploy_error = None
    deployment_run_id = None
    try:
        deployment_id = db.get_release_uat_deployment_id(settings, str(prep["project_id"]))
        bundle = (
            db.get_deployment_for_agent(settings, deployment_id, str(worker["org_id"]))
            if deployment_id
            else None
        )
        if not bundle:
            deploy_error = "no UAT deployment designated for this project"
        else:
            deployment_run_id = deploy.launch_release_uat_deploy(
                settings,
                release,
                bundle["deployment"],
                bundle["server"],
                bundle["project"] or {},
                worker.get("name") or "release",
            )
            db.update_release(
                settings,
                str(release["id"]),
                {"status": "deploying", "uat_deployment_run_id": deployment_run_id},
            )
    except Exception as e:  # noqa: BLE001 — must not raise past a committed submit
        deploy_error = getattr(e, "message", str(e)) or e.__class__.__name__

    if deploy_error:
        db.update_release(
            settings,
            str(release["id"]),
            {"status": "uat-deploy-failed", "failure_reason": deploy_error[:500]},
        )

    return {
        "ok": True,
        "version": version,
        "release_id": str(release["id"]),
        "test_cases_attached": attached,
        "test_cases_inherited": inherited,
        "document_id": doc_id,
        "deployment_run_id": deployment_run_id,
        "deploy_error": deploy_error,
    }
