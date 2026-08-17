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

import json
from typing import Any

from . import db, deploy, documents, release_notes
from .config import Settings


def briefing(settings: Settings, prep_id: str, worker: dict[str, Any]) -> dict[str, str]:
    """The project's own words for this job.

    us-103.2 moved this out of `claim`: a runner re-adopting a prep it already
    holds needs the identical briefing, and re-claiming to get it is not
    available — it is already claimed, by itself.

    us-101.6: the claim is where the project's own words reach this job.
    Instruction delivery everywhere else is keyed on a `runs` row, and a
    release prep deliberately has none — so the project's Release instruction,
    editable in the app and published to `.buildmill/Release_Prep.md` since
    us-99.1, reached nobody at all. The only text steering a release agent was
    a string in the runner.

    Read LIVE rather than snapshotted at dispatch (us-100.5's rule): a prep
    claimed a day after it was queued should be steered by today's text.
    """
    instruction = ""
    agent_instructions = ""
    try:
        prep = db.get_release_prep(settings, prep_id, str(worker["org_id"]))
        files = (
            db.get_project_instruction_files(settings, str(prep["project_id"]))
            if prep
            else None
        ) or {}
        instruction = (files.get("instructions") or {}).get("release") or ""
        agent_instructions = files.get("agent_instructions") or ""
    except Exception:  # noqa: BLE001 — a claim must not fail over its briefing
        pass

    return {
        "instruction": instruction,
        "agent_instructions": agent_instructions,
        # Generated from the section and block definitions the renderer uses,
        # so the text telling an agent what it may write and the page drawing
        # it cannot describe different things.
        "notes_vocabulary": release_notes.vocabulary_brief(),
    }


async def claim(
    settings: Settings, prep_id: str, worker: dict[str, Any]
) -> dict[str, Any]:
    row = db.claim_release_prep(settings, prep_id, worker)
    if not row:
        return {"error": "not available to claim — already claimed or not queued"}
    return {
        "ok": True,
        "id": prep_id,
        "release_id": str(row["release_id"]),
        **briefing(settings, prep_id, worker),
    }


def not_running_error(status: str) -> str:
    """Why a prep that is no longer `running` refuses work, in words.

    us-103.1/103.3: this refusal is read in an agent's transcript, and it is
    the only place the agent learns that the job was taken away from it. "is
    failed, not running" told nobody anything; each of these states has a
    cause and the cause is what stops a retry loop.
    """
    return {
        "cancelled": "the manager stopped this release — nothing submitted "
        "here will be recorded. Do not retry.",
        "failed": "this release prep was failed after its claim expired — the "
        "agent holding it stopped reporting. The release is waiting for the "
        "manager to retry it, which dispatches a fresh job. Do not retry.",
        "succeeded": "this release prep has already been submitted.",
        "queued": "this release prep is not claimed — claim it first.",
    }.get(status, f"release prep is {status}, not running")


async def submit(
    settings: Settings,
    prep_id: str,
    worker: dict[str, Any],
    notes_summary: str,
    notes_detail: str,
    test_cases: list[dict[str, Any]] | None = None,
    proposed_version: str | None = None,
    version_rationale: str | None = None,
    notes_doc: Any = None,
    uncovered: list[str] | None = None,
) -> dict[str, Any]:
    prep = db.get_release_prep(settings, prep_id, str(worker["org_id"]))
    if not prep:
        return {"error": "release prep not found"}
    if str(prep.get("worker_id") or "") != str(worker["id"]):
        return {"error": "you do not hold this release prep"}
    if prep["status"] != "running":
        return {"error": not_running_error(prep["status"])}

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

    # us-100.6's version proposal, checked HERE rather than only in the MCP
    # tool. us-101.2 found the HTTP transport had no way to send one at all;
    # putting the rule at the choke point is what stops the two from
    # disagreeing again, which is this module's whole reason for existing.
    if (proposed_version or "").strip() or (version_rationale or "").strip():
        # Imported here, not at module scope: factory_mcp imports this module.
        from .factory_mcp import version_proposal_error

        bad = version_proposal_error(
            proposed_version or "",
            version_rationale or "",
            db.release_versions_for_prep(settings, prep_id),
        )
        if bad:
            hint = bad.get("hint")
            return {"error": bad["error"] + (f" — {hint}" if hint else "")}

    # us-101.3: every check is a step and an expectation, and the release
    # accounts for what it shipped. Checked BEFORE anything is written, for
    # the reason us-100.6 established for the version proposal: a refused
    # hand-back must never half-complete the job. Every failure is collected
    # and returned at once — one rule per re-run is one agent session per
    # rule.
    items = list(release.get("included_items") or [])
    inherited_ids = db.release_inheritable_display_ids(
        settings,
        str(worker["org_id"]),
        str(prep["project_id"]),
        items,
    )
    checked, case_errors = release_notes.check_cases(
        test_cases,
        included=items,
        inherited_display_ids=inherited_ids,
        uncovered=uncovered,
    )
    if case_errors:
        return {"error": "\n".join(["this release's checks are not ready:", *case_errors])}

    # us-101.4: the notes declaration is coerced, never refused — findings are
    # advice handed back with a successful submit.
    doc, doc_findings = release_notes.as_declaration(notes_doc)

    # US-21.4: the release's test-case set is ASSEMBLED, not invented — every
    # included work item's active cases come across first, then whatever the
    # agent authored sits alongside them.
    inherited = db.attach_release_inherited_cases(settings, str(release["id"]))
    attached = 0
    if checked:
        attached = db.attach_release_test_cases(
            settings,
            release_id=str(release["id"]),
            org_id=str(worker["org_id"]),
            project_id=str(prep["project_id"]),
            cases=checked,
        )

    db.update_release(
        settings,
        str(release["id"]),
        {
            "notes_summary": notes_summary,
            "notes_detail": notes_detail,
            # us-113.1: encoded here, the way every other jsonb write in db.py
            # does it. A raw dict is not a psycopg parameter — it raised
            # `cannot adapt type 'dict'` client-side and broke every release
            # prep for a day. `update_release` guards this too; both, because
            # the guard protects future callers and this keeps the patch
            # itself honest.
            "notes_doc": json.dumps(doc),
            "status": "notes-ready",
            # us-100.6: advisory. `releases.version` is untouched — a proposal
            # is an input to the manager's cut, never the cut itself.
            **(
                {
                    "proposed_version": proposed_version,
                    "version_rationale": version_rationale,
                }
                if proposed_version
                else {}
            ),
        },
    )
    db.stamp_release_milestones(
        settings,
        str(release["id"]),
        notes_written=True,
        cases_attached=bool(attached or inherited),
    )

    doc_id = ""
    doc_error = None
    try:
        exported = await documents.create_or_replace(
            settings,
            org_id=str(worker["org_id"]),
            project_id=str(prep["project_id"]),
            name=f"release-notes-{version.lower()}.md",
            # us-101.4: rendered FROM the declaration, so the exported file
            # and the page cannot say different things.
            content=release_notes.render_markdown(
                version, notes_summary, notes_detail, doc, checked
            ).encode(),
            source="agent",
            attached_to="project",
            mime_type="text/markdown",
        )
        doc_id = str(exported.get("id") or "")
    except Exception as e:  # noqa: BLE001
        # A failed document write must not fail a release whose notes and
        # test cases are already recorded on the release row. us-101.4: it
        # must not be SILENT either — this swallowed everything and left
        # doc_id = "", so a release could finish with no exported notes and
        # nothing anywhere saying why.
        doc_id = ""
        doc_error = getattr(e, "message", str(e)) or e.__class__.__name__

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
        "document_error": doc_error,
        "notes_doc_findings": doc_findings,
        "deployment_run_id": deployment_run_id,
        "deploy_error": deploy_error,
    }
