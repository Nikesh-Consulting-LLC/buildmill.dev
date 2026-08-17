"""Worker pool endpoints (US-3.2) — one claim contract for every worker.

A worker (the autonomous runner or a person's own tool) authenticates
with its registry token (US-3.1) via X-Worker-Token, claims from the
org's ready pool, pulls a one-call context bundle, and hands work back
through submit. Code submits are verified against GitHub via the App —
the factory opens the PR and pulls the diff itself; workers never hold
GitHub credentials (the git remote is US-3.8).
"""

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, field_validator

from .. import (
    artifacts_sim,
    changesets,
    db,
    documents,
    github,
    github_tokens,
    issue_sync,
    mcp_tools,
    model_resolution,
    project_env,
    reconcile,
    release_prep,
    validation,
)
from ..config import Settings, get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/worker", tags=["worker"])


def verify_worker(
    x_worker_token: str = Header(default=""),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    worker = db.get_worker_by_token(settings, x_worker_token)
    if not worker:
        raise HTTPException(status_code=401, detail="invalid or revoked worker token")
    return worker


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _tool_bundle(
    settings: Settings,
    request: Request,
    run: dict[str, Any],
    worker: dict[str, Any],
) -> dict[str, Any]:
    """The MCP half of the work-context bundle.

    US-34.2: a credentialed server is reached through the factory's proxy with a
    key minted for THIS run; a credential-free stdio server runs locally, because
    a proxy hop for a Playwright browser would add latency and a failure mode and
    protect nothing. Either way the entry had to be granted, so the agent's tool
    surface is still fully described by the factory.
    """
    surface = run.get("tool_surface") or {}
    granted = surface.get("granted") or []
    if not granted:
        # Default deny (us-34.3): the factory server and nothing else, which is
        # exactly what us-31.9 ships. No key is minted for a run with no grants.
        return {"tool_servers": [], "tool_notes": mcp_tools.surface_notes(surface)}
    base = str(request.base_url).rstrip("/")
    key = None
    if any(g.get("proxied") for g in granted):
        try:
            key = db.mint_mcp_key(
                settings,
                str(worker["org_id"]),
                str(worker["id"]),
                str(run["id"]),
            )
        except Exception:  # noqa: BLE001 — a run without tools still runs
            logger.warning("could not mint an MCP key for %s", run["id"])
    servers = []
    for entry in granted:
        if entry.get("proxied"):
            if not key:
                continue
            servers.append(
                {
                    "slug": entry.get("slug"),
                    "name": entry.get("name"),
                    "transport": "http",
                    "url": f"{base}/api/v1/mcp-proxy/{entry.get('slug')}",
                    # The scoped key, NOT the server's credential. This is the
                    # whole point of the proxy.
                    "key": key,
                    "tools": entry.get("tools") or [],
                    "audited": True,
                }
            )
        else:
            server = db.get_mcp_server(settings, str(entry["id"]))
            servers.append(
                {
                    "slug": entry.get("slug"),
                    "name": entry.get("name"),
                    "transport": "stdio",
                    "command": (server or {}).get("command"),
                    "tools": entry.get("tools") or [],
                    # US-34.4: it never passes through the proxy, so it cannot be
                    # recorded there. Said, not implied.
                    "audited": False,
                }
            )
    return {"tool_servers": servers, "tool_notes": mcp_tools.surface_notes(surface)}


def stamp_run_settings(
    settings: Settings, run: dict[str, Any], worker: dict[str, Any]
) -> dict[str, Any]:
    """US-32.7: resolve this run's effective settings and record them on it.

    Called at claim, once, server-side — the runner is told what to do rather
    than working it out, because a runner that resolved its own settings would
    be a second implementation of the precedence rules and the two would
    disagree. The supervisor and manager layers are read from the run itself
    (us-33.4 and us-33.5 are what write them); today they are usually absent,
    and the resolver's precedence handles that without a special case.
    """
    org_id = str(worker["org_id"])
    config = db.get_runner_config(settings, str(worker["id"]))
    kind = run.get("kind") or "code"
    overrides = (run.get("input_context") or {}).get("settings_override") or {}

    # US-33.4: a retry after a work-fault escalates a step up the ladder the
    # presets declare, using the SUPERVISOR layer us-32.7 already resolves and
    # records — so the escalation is visible and explainable by construction
    # rather than being a hidden behaviour of the retry path. An explicit
    # supervisor override in the run's context wins: something already decided.
    supervisor = overrides.get("supervisor")
    escalation_reason = None
    if not supervisor:
        supervisor, escalation_reason = db.escalation_for(
            settings,
            org_id,
            str(run["issue_id"]) if run.get("issue_id") else None,
            kind,
        )

    # us-116.1: the resolver's inputs are built in ONE place, shared with the
    # session path — the two used to disagree because the session had its own
    # two-line copy of these rules.
    inputs = model_resolution.load_inputs(settings, org_id, config=config)
    resolved = model_resolution.resolve_for_kind(
        inputs,
        kind,
        supervisor_override=supervisor,
        manager_override=overrides.get("manager"),
    )
    record = resolved.as_record()

    # US-53.1: billing is the AGENT's switch, not a resolved setting. Stamped
    # `subscription` only when the agent both chose it and its live session
    # declared the capability — an old supervisor will mint a metered key no
    # matter what the config says, and the record must match what happens.
    record["billing"] = (
        "subscription"
        if config.get("claude_billing") == "subscription"
        and db.worker_session_declares_auth(settings, str(worker["id"]))
        else "metered"
    )

    # US-39.2: a batch code run carries several stories and was being given the
    # turn count of one. Observed 2026-07-27: a run exited at 851s -- well
    # inside its time -- with "Reached max turns (40)", having done a fraction
    # of the work, and the repair loop then retried it into the same wall.
    #
    # Scaled AFTER resolution on purpose: `max_turns` is validated 1..500 as a
    # PRESET value, which governs what a human may type. What an eight-story run
    # resolves to is a different question, so the product is bounded by its own
    # constant rather than by that range.
    units = db.run_work_units(settings, str(run["id"]))
    if units > 1:
        turns = record["resolved_settings"].get("max_turns")
        if isinstance(turns, int) and turns > 0:
            scaled = min(turns * units, db.MAX_SCALED_TURNS)
            record["resolved_settings"]["max_turns"] = scaled
            # Say it on the trace: a run whose limits differ from its preset's
            # should never leave the manager comparing two numbers by hand.
            try:
                db.record_run_trace(
                    settings,
                    str(run["id"]),
                    str(worker["id"]),
                    "settings",
                    f"this run carries {units} stories, so its turn allowance is "
                    f"{turns} x {units} = {scaled}",
                )
            except Exception:  # noqa: BLE001 — a narration must not cost a claim
                logger.warning("could not record the scaled turn count for %s", run["id"])

    db.record_run_settings(settings, str(run["id"]), record)
    if escalation_reason:
        # The run trace says WHAT escalated and WHY — that line is the whole
        # value of the feature when the manager is reading afterwards.
        try:
            db.record_run_trace(
                settings,
                str(run["id"]),
                str(worker["id"]),
                "settings",
                escalation_reason,
            )
        except Exception:  # noqa: BLE001 — a narration must not cost a claim
            logger.warning("could not record the escalation reason for %s", run["id"])

    # us-116.7: an interactive agent cannot start without a model — its CLI's
    # config needs a model block, and the runner refuses (US-78.5). With the
    # org's default provider model as the resolver's floor, reaching here with
    # nothing means the org chose nothing anywhere. Fail the run HERE, with the
    # three-place sentence, rather than let the runner spend a claim to say
    # "nothing was spent". Other modules keep today's behaviour: a null model
    # means the gateway answers with the org default at call time.
    model = record["resolved_settings"].get("model")
    if not model and "interactive" in (config.get("enabled_modules") or []):
        reason = model_resolution.no_model_refusal(
            str(worker.get("name") or ""), [kind], inputs.org_default
        )
        db.fail_run_minimal(settings, str(run["id"]), reason, worker.get("name"))
        raise HTTPException(status_code=409, detail=reason)

    # US-32.8: the gateway resolves a provider FROM the model id (us-27.8), so a
    # model no provider offers routes nowhere. Fail the run here, naming the
    # model, rather than letting the agent spend a lease discovering it — and
    # rather than refusing the claim, which would loop the run forever.
    if model:
        providers, _routes = db.get_org_llm_config(settings, org_id)
        offered: set[str] = set()
        for p in providers:
            offered.update(p.get("models") or [])
            if p.get("default_model"):
                offered.add(str(p["default_model"]))
        if model not in offered:
            reason = (
                f"this run resolved to the model '{model}', which no LLM "
                "provider in this org offers — the model is what decides which "
                "provider answers, so the call would route nowhere. Fix it on "
                "the agent's settings page or the preset it uses."
            )
            db.fail_run_minimal(settings, str(run["id"]), reason, worker.get("name"))
            raise HTTPException(status_code=409, detail=reason)
    return record


@router.get("/pool")
async def pool(
    worker: dict = Depends(verify_worker),
    settings: Settings = Depends(get_settings),
):
    # expired-with-pushes claims auto-submit before the pool is listed —
    # the listing stays self-healing without a background scheduler
    try:
        await reconcile.reconcile_pushed_expired_claims(settings)
    except Exception:  # noqa: BLE001 — a reconciler hiccup never blocks the pool
        pass
    runs = db.list_worker_pool(settings, worker)
    # US-59.9: this worker's own parked runs ride the same response as the
    # ordinary pool — the runner checks these FIRST (resume before claiming
    # fresh work), so a paused run is never left waiting behind an endless
    # stream of new items landing on the same machine.
    try:
        resumable = db.list_worker_resumable(settings, worker)
    except Exception:  # noqa: BLE001 — the ordinary pool still answers
        logger.warning("could not list resumable runs for worker %s", worker["id"])
        resumable = []
    return {
        "runs": [
            {
                "id": str(r["id"]),
                "kind": r["kind"],
                "issue_id": str(r["issue_id"]),
                "issue_title": r["issue_title"],
                "issue_type": r["issue_type"],
                "project_name": r["project_name"],
                "repo_full_name": r["repo_full_name"],
            }
            for r in runs
        ],
        "resumable": [
            {
                "id": str(r["id"]),
                "kind": r["kind"],
                "status": r["status"],
                "issue_id": str(r["issue_id"]) if r["issue_id"] else None,
                "issue_title": r["issue_title"],
                "resume_reason": r["resume_reason"],
            }
            for r in resumable
        ],
    }


@router.post("/runs/{run_id}/resume-claim")
async def resume_claim(
    run_id: str,
    worker: dict = Depends(verify_worker),
    settings: Settings = Depends(get_settings),
):
    """US-59.3/59.4/59.9: continue a paused/awaiting_input run this worker
    already owns. Distinct from `/claim` because the semantics differ — this
    is affinity-scoped (only the owning worker_id may call it) rather than
    org-scoped-and-first-come, and it never touches issue status (the work
    was never released, only parked)."""
    run = db.resume_claim(settings, run_id, worker)
    if run:
        return {
            "run": {
                "id": str(run["id"]),
                "kind": run["kind"],
                "claim_expires_at": _iso(run["claim_expires_at"]),
            }
        }
    raise HTTPException(
        status_code=409,
        detail="not resumable — already resumed, or not this worker's run",
    )


@router.post("/runs/{run_id}/claim")
async def claim(
    run_id: str,
    worker: dict = Depends(verify_worker),
    settings: Settings = Depends(get_settings),
):
    # US-31.3: fail-closed capability gate — the refusal names exactly which
    # grant is missing, because "not on the project" and "on the project but
    # not for this kind" have different fixes.
    refusal = db.worker_run_refusal(settings, str(worker["id"]), run_id)
    if refusal:
        raise HTTPException(status_code=403, detail=refusal)
    run = db.claim_run(settings, run_id, worker)
    if run:
        # US-32.7: resolve the run's settings ONCE, here, and stamp them. Work
        # is claimed from a pool, so the same story handed to two agents can
        # run two different ways — nothing else would say which.
        try:
            stamp_run_settings(settings, run, worker)
        except HTTPException:
            # US-32.8's unroutable-model refusal is deliberate — the run has
            # already been failed with the reason, and the agent must hear it.
            raise
        except Exception:  # noqa: BLE001 — a missing stamp never loses a claim
            logger.warning("could not stamp run settings for %s", run_id)
        return {
            "run": {
                "id": str(run["id"]),
                "kind": run["kind"],
                "claim_expires_at": _iso(run["claim_expires_at"]),
            }
        }
    existing = db.get_worker_run(settings, run_id, str(worker["org_id"]))
    if existing:
        raise HTTPException(
            status_code=409, detail="someone else took it — list the pool again"
        )
    raise HTTPException(status_code=404, detail="run not found")


@router.get("/runs/{run_id}/context")
async def context(
    run_id: str,
    request: Request,
    worker: dict = Depends(verify_worker),
    settings: Settings = Depends(get_settings),
):
    run = db.get_worker_run(settings, run_id, str(worker["org_id"]))
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    if str(run.get("worker_id") or "") != str(worker["id"]):
        raise HTTPException(
            status_code=409, detail="you do not hold this run — claim it first"
        )
    db.extend_claim(settings, run_id, str(worker["id"]))

    ic = run.get("input_context") or {}
    # US-5.14: instructions = code-generated mechanics (always present,
    # edit-proof) + the project's editable behavioral template, read live.
    template = db.get_worker_instruction(
        settings,
        str(run.get("project_id") or ""),
        run["kind"],
        issue_id=str(run.get("issue_id") or "") or None,
    )
    # US-5.12: the work item's comment thread, oldest first.
    comments = db.list_issue_comments_for_run(
        settings, run_id, str(worker["org_id"])
    )
    if run["kind"] == "prd":
        mechanics = (
            "This is a PRD-drafting run — no repo, no branch, no git remote. "
            "Submit the PRD markdown with `prd`."
        )
        return {
            "run_id": str(run["id"]),
            "kind": run["kind"],
            "issue_id": str(run["issue_id"]),
            "context": ic,
            "instructions": mechanics + (f"\n\n{template}" if template else ""),
            "instruction_set": run.get("instruction_set"),
            "comments": comments,
            # US-32.7: what this run was resolved to run under. The runner
            # performs no precedence logic of its own — it is told.
            "run_settings": run.get("resolved_settings") or {},
            # US-59.3: present only when this run has a captured session —
            # the module appends `--resume <id>` when it has one and the
            # module supports it, and simply does not when this is null.
            "resume_session_id": run.get("claude_session_id"),
        }

    base = str(request.base_url).rstrip("/")
    # US-7.3: strategy-resolved working branch, stored for hand-back matching.
    branch, _dev_strategy, submit_mode = db.resolve_working_branch(settings, run)
    if run["kind"] == "code":
        db.set_run_branch_ref(settings, str(run["id"]), branch)
    mechanics = (
        f"Clone the factory git remote (HTTP Basic auth — password is this "
        f"same worker token), work on branch '{branch}', push it, then "
        f"submit with the branch ref. No GitHub credentials"
        + (
            " and no PR needed — the factory opens the PR itself on submit."
            if submit_mode == "pr"
            else " — this project commits directly to the default branch, no PR."
        )
    )
    return {
        "run_id": str(run["id"]),
        "kind": run["kind"],
        "issue_id": str(run["issue_id"]),
        "context": ic,
        "branch_name": branch,
        "git_remote_url": (
            f"{base}/git/{run['org_shortname']}/{run['project_slug']}.git"
        ),
        "repo_full_name": ic.get("repo_full_name"),
        "default_branch": ic.get("default_branch"),
        "instructions": mechanics + (f"\n\n{template}" if template else ""),
        "instruction_set": run.get("instruction_set"),
        "comments": comments,
        # US-31.2: the lease this claim runs under, so the agent can keep its
        # own CLI limit strictly below the time it actually holds the run.
        # US-39.2: read from the claim's own `claim_expires_at`, not recomputed.
        # A parallel calculation here would tell the runner it has time the
        # claim will not honour once the claim scales with the work.
        "lease_seconds": db.worker_lease_seconds(
            settings,
            str(worker["id"]),
            worker.get("type") or "autonomous",
            str(run.get("id")) if run.get("id") else None,
        ),
        # US-31.8: the workspace is the project's, so the runner needs the
        # project id to derive its folder.
        "project_id": str(run.get("project_id") or "") or None,
        # US-32.7: the settings this run was resolved to, with no precedence
        # logic left for the runner to get wrong.
        "run_settings": run.get("resolved_settings") or {},
        # US-59.3: see the prd branch above — null on an ordinary claim,
        # the id to `--resume` on a resumed one.
        "resume_session_id": run.get("claude_session_id"),
        # US-34.2/34.3: the tool servers this run was granted, and a key worth
        # exactly this run. The credential for each one stays in the factory —
        # the agent gets a proxy URL and a scoped key, never a secret.
        **_tool_bundle(settings, request, run, worker),
        # US-89.2: the project's defined environment, resolved for this
        # agent (agent-scoped entries win). Becomes real process env at CLI
        # spawn on the runner — never a file in the workspace.
        **(await _environment_bundle(settings, run, worker)),
    }


async def _environment_bundle(
    settings: Settings, run: dict[str, Any], worker: dict[str, Any]
) -> dict[str, Any]:
    """{environment, environment_catalog} for the run's project, or {} when
    the project defines nothing. Best-effort: an unreadable environment must
    not stop a run — the agent can still ask get_environment and see why."""
    project_id = str(run.get("project_id") or "")
    if not project_id:
        return {}
    try:
        values, catalog = await project_env.effective_env(
            settings, project_id, str(worker["id"])
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("environment bundle failed for run %s: %s", run.get("id"), e)
        return {}
    if not values and not catalog:
        return {}
    return {"environment": values, "environment_catalog": catalog}


@router.post("/runs/{run_id}/heartbeat")
async def heartbeat(
    run_id: str,
    worker: dict = Depends(verify_worker),
    settings: Settings = Depends(get_settings),
):
    if not db.extend_claim(settings, run_id, str(worker["id"])):
        raise HTTPException(
            status_code=409, detail="no live claim on this run to extend"
        )
    return {"ok": True}


class CommentBody(BaseModel):
    body: str


async def perform_add_comment(
    settings: Settings, worker: dict[str, Any], run_id: str, body: str
) -> dict[str, Any]:
    """US-5.12: a claim-holder comments on its run's work item — shared by
    the REST endpoint and the MCP add_comment tool. Posting extends the
    lease, same as a heartbeat."""
    if not body.strip():
        raise HTTPException(status_code=422, detail="comment body is required")
    run = db.get_worker_run(settings, run_id, str(worker["org_id"]))
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    if str(run.get("worker_id") or "") != str(worker["id"]):
        raise HTTPException(
            status_code=409, detail="you do not hold this run — claim it first"
        )
    db.extend_claim(settings, run_id, str(worker["id"]))
    row = db.add_worker_comment(settings, run, worker, body.strip())
    return {"ok": True, "comment_id": str(row["id"])}


@router.post("/runs/{run_id}/comment")
async def add_comment(
    run_id: str,
    body: CommentBody,
    worker: dict = Depends(verify_worker),
    settings: Settings = Depends(get_settings),
):
    return await perform_add_comment(settings, worker, run_id, body.body)


# US-42.1 lives in artifacts_sim now, because the same coercion has to run
# on cases parsed out of a test plan as on cases posted to this endpoint —
# two implementations is how they drift. Why it coerces instead of refusing:
# a request-body validation error discards the *whole* hand-back, so the
# 2026-07-28 plan batch lost fifteen submissions (plan, test plan, notes and
# token counts) to the shape of one field, retried, and in the process burned
# two leases and had one run double-claimed.
_as_text = artifacts_sim.as_text
_as_str_list = artifacts_sim.as_str_list


class AgentTestCase(BaseModel):
    title: str
    steps: str = ""
    expected_result: str = ""
    test_types: list[str] = []
    environments: list[str] = []

    # US-42.1: shape-tolerant, both directions. `title` stays strict — a
    # case with no title is not a formatting difference, it is a case the
    # manager cannot read in UAT.
    @field_validator("steps", "expected_result", mode="before")
    @classmethod
    def _coerce_text(cls, v: Any) -> Any:
        return _as_text(v)

    @field_validator("test_types", "environments", mode="before")
    @classmethod
    def _coerce_str_list(cls, v: Any) -> Any:
        return _as_str_list(v)


def _strip_nul(value: Any) -> Any:
    """US-31.1: Postgres text cannot carry 0x00, and psycopg refuses it
    client-side — so one NUL byte anywhere in a CLI's output turned a
    failure report into a 500, and the failure into a silent lease loop.
    Strip it at the boundary, recursively, before anything touches the DB."""
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {k: _strip_nul(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_strip_nul(v) for v in value]
    return value


class Submit(BaseModel):
    # US-31.1: every inbound string is NUL-stripped before validation.
    @field_validator("*", mode="before")
    @classmethod
    def _no_nul_bytes(cls, v: Any) -> Any:
        return _strip_nul(v)

    # US-59.1: captured from the CLI's stream, sent on every submit —
    # success, failure, or a pause alike.
    claude_session_id: str | None = None
    # US-59.3: set by the runner (via repair.turn_limit_hit) when this
    # non-success submit is a turn-limit exit, not an ordinary failure —
    # the one signal that changes how `error` below is landed.
    pause_reason: str | None = None
    # plan runs
    plan: str | None = None
    test_plan: str | None = None
    # prd runs
    prd: str | None = None
    # breakdown runs (US-2.33): the proposed story split
    stories: list[dict[str, Any]] | None = None
    # code runs
    branch_ref: str | None = None
    notes: str | None = None
    test_cases: list[AgentTestCase] | None = None
    # simulated provider only (US-3.5 parity): a simulated:// pr_url may
    # carry its own diff; real runs never post either.
    pr_url: str | None = None
    diff: str | None = None
    # failure report — the old callback's failed outcome (US-3.5)
    error: str | None = None
    # US-10.11: work-fault (story wrong) vs runner-fault (machine broken)
    fault_class: str | None = None
    # both kinds
    stdout: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    # US-32.4: settings that were resolved for this run but that the module
    # driving it could not express. A run that was told to think harder,
    # didn't, and said nothing about it is the failure this closes.
    settings_not_delivered: list[str] | None = None


class Release(BaseModel):
    note: str | None = None


async def _attribute_branch_coverage(
    settings: Settings,
    token: str,
    owner: str,
    repo: str,
    base: str,
    branch: str,
    run_id: str,
    org_id: str,
    members: list[dict[str, Any]],
) -> int:
    """US-40.2: record which stories the branch's commits landed, read from
    GitHub. Returns the number of coverage rows written.

    Read from GitHub and never from the hand-back body, for the same reason
    the diff is (US-3.2): a worker that could assert its own coverage could
    manufacture a story's completion with no code behind it, which is the one
    outcome us-27.1's fan-out exists to prevent.

    `compare_commits` rather than a branch listing, so this sees the commits
    the branch ADDS and not the whole history behind it.
    """
    try:
        comparison = await github.compare_commits(token, owner, repo, base, branch)
    except github.GitHubError:
        logger.warning(
            "could not list commits on %s for run %s — no coverage recorded",
            branch,
            run_id,
        )
        return 0

    written = 0
    for commit in comparison.get("commits") or []:
        sha = commit.get("sha")
        message = (commit.get("commit") or {}).get("message") or ""
        named = changesets.story_trailers(message)
        if not (sha and named):
            continue
        # An id that is not in this run is dropped rather than guessed at.
        resolved, _unknown = db.resolve_member_ids(members, named)
        if not resolved:
            continue
        written += db.record_changeset_coverage(
            settings,
            run_id,
            org_id,
            resolved,
            sha,
            message.splitlines()[0] if message else "",
        )
    return written


def _store_handback_notes(
    settings: Settings,
    run: dict[str, Any],
    worker: dict[str, Any],
    run_id: str,
    notes: str | None,
) -> None:
    """US-13.3: hand-back notes ride the submission as data — stored on
    the run for the review surface and mirrored into the work item's
    thread so they survive the run. Best-effort: a storage hiccup never
    fails a submit that already completed."""
    text = (notes or "").strip()
    if not text:
        return
    try:
        db.set_run_handback_notes(settings, run_id, text)
        # US-13.12: an issue-less (project-scoped) run has no work-item
        # thread — the run-level notes are its record.
        if run.get("issue_id"):
            db.add_worker_comment(
                settings, run, worker,
                "**Notes for the manager (hand-back):** " + text,
            )
    except Exception:  # noqa: BLE001 — the submission already succeeded
        logger.warning("handback notes not stored for run %s", run_id)


async def _maybe_auto_approve(
    settings: Settings,
    run: dict[str, Any],
    body: "Submit",
    merge_ctx: tuple[str, str, str, str | None] | None = None,
) -> dict[str, Any] | None:
    """US-17.4: when the project's matching auto-approve switch is on, clear the
    gate the just-submitted run reached through the same effects a manual
    approval produces — attributed to the setting, never a person — and
    auto-dispatch the next eligible run. Never raises: a promotion failure must
    not fail the worker's submit. Never triggers a deployment."""
    kind = run["kind"]
    if kind not in ("prd", "plan", "code"):
        return None
    flags = db.get_project_auto_flags(settings, str(run["project_id"]))
    if not flags.get(kind):
        return None
    issue_id = str(run["issue_id"])
    try:
        if kind == "prd":
            return db.auto_approve_prd(settings, issue_id)
        if kind == "plan":
            summary = db.auto_approve_plan(settings, issue_id)
            if body.test_plan:
                cases = artifacts_sim.parse_test_plan_cases(body.test_plan)
                summary["materialized_test_cases"] = db.materialize_test_cases(
                    settings,
                    str(run["org_id"]),
                    str(run["project_id"]),
                    issue_id,
                    cases,
                )
            return summary
        # code: merge the PR (auto-merge is the point of the switch), then record
        # the same approval effects. Deployment is never auto-triggered.
        if merge_ctx is None:
            return None
        token, owner, repo, pr_url = merge_ctx
        merged = "simulated"
        if pr_url and not pr_url.startswith("simulated://"):
            _o, _r, number = github.parse_pr_url(pr_url)
            sha = await github.merge_pull_request(token, owner, repo, number)
            if sha:
                db.set_run_merge_sha(settings, str(run["id"]), sha)
                merged = "merged"
        db.auto_approve_code(settings, str(run["id"]))
        # US-81.5: a real merge applies the run's case→spec map.
        if merged == "merged":
            db.apply_spec_map(settings, str(run["id"]))
        return {"gate": "code", "merged": merged}
    except Exception as e:  # noqa: BLE001 — never fail the submit on auto-approve
        logger.warning(
            "auto-approve after %s submit failed for run %s: %s",
            kind,
            run.get("id"),
            e,
        )
        return {"gate": kind, "error": str(e)}


async def perform_submit(
    settings: Settings,
    worker: dict[str, Any],
    run_id: str,
    body: Submit,
    trigger: str = "submit",
) -> dict[str, Any]:
    """The submit contract, shared by the REST endpoint, the MCP tools
    (US-3.3), and the lease-expiry reconciler (US-3.4). Raises
    HTTPException with actionable detail."""
    run = db.get_worker_run(settings, run_id, str(worker["org_id"]))
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    if run.get("worker_id") is None and run.get("status") == "queued":
        raise HTTPException(
            status_code=409,
            detail="the run went back to the pool — claim it again, then submit",
        )
    if str(run.get("worker_id") or "") != str(worker["id"]):
        raise HTTPException(
            status_code=409, detail="another worker holds this run"
        )
    if run.get("status") == "succeeded":
        # idempotency (US-3.4): explicit submit and lease-expiry auto-submit
        # can arrive in any order — whichever comes later is a no-op.
        return {
            "ok": True,
            "idempotent": True,
            "pr_url": run.get("pr_url"),
            "issue_status": (
                "in-review" if run["kind"] == "code"
                else "prd-review" if run["kind"] == "prd"
                else "ready" if run["kind"] == "breakdown"
                else "plan-review"
            ),
        }

    # US-32.4: a setting the module could not express goes on the run's own
    # record, server-side. The runner also traces it over the socket, but that
    # is fire-and-forget by design — this write is the one that has to survive.
    for line in (body.settings_not_delivered or [])[:20]:
        try:
            db.record_run_trace(
                settings, run_id, str(worker["id"]), "settings", str(line)[:400]
            )
        except Exception:  # noqa: BLE001 — never fail a hand-back over a note
            logger.warning("could not record undelivered setting for %s", run_id)

    # US-34.2: the scoped MCP key is worth one run, so it dies here. `validate`
    # already refuses a key whose run is not running — this closes the window
    # between the hand-back and that status change.
    try:
        db.revoke_mcp_keys_for_run(settings, run_id)
    except Exception:  # noqa: BLE001 — expiry and the status check both still hold
        logger.warning("could not revoke the MCP keys for %s", run_id)

    usage = {
        "tokens_in": body.tokens_in,
        "tokens_out": body.tokens_out,
        "cost_usd": body.cost_usd,
    }

    if body.error:
        # US-59.5: a run that asked and then stopped — for any reason —
        # parks on the question rather than landing as a failure. Checked
        # before the turn-limit/stopped-ceiling reads below because "waiting
        # on a human" is the more actionable fact when both are true (the
        # agent asked, then kept working into its own turn cap).
        if db.has_pending_clarification(settings, run):
            ok = db.awaiting_input_run(
                settings,
                run_id,
                claude_session_id=body.claude_session_id,
                stdout=body.stdout,
                worker_name=worker["name"],
            )
            if not ok:
                raise HTTPException(
                    status_code=409, detail="run is not claimed — claim it and retry"
                )
            # US-59.5: the work item's own status is untouched — parking is
            # not a fault, so `issue_status` (which callers read as "what did
            # the WORK ITEM become") would be a lie here. `run_status` is the
            # honest field for what actually changed.
            return {"ok": True, "run_status": "awaiting_input"}

        # US-59.3: the runner's own turn-limit reading — not a fault, a
        # work-progress signal. Bounded server-side by `pause_run`'s own
        # attempt cap, so this never loops forever even if the runner keeps
        # reporting the same thing.
        if body.pause_reason == "turn_limit" and not run.get("stopped_reason"):
            _accepted, landed, attempts, cap = db.pause_run(
                settings,
                run_id,
                reason="turn_limit",
                claude_session_id=body.claude_session_id,
                stdout=body.stdout,
                error=body.error,
                worker_name=worker["name"],
            )
            if landed == "paused":
                # US-59.3: same reasoning as the awaiting_input branch above —
                # the issue itself hasn't moved, only the run has.
                return {"ok": True, "run_status": "paused"}
            if landed == "":
                raise HTTPException(
                    status_code=409, detail="run is not claimed — claim it and retry"
                )
            # `landed == "exhausted"`: resume attempts are spent — fall
            # through to the ordinary failure path below (fault class,
            # issue sync, incident), naming why so the manager reads "ran
            # out of turns, then ran out of resumes" rather than a bare
            # failure.
            body = body.model_copy(
                update={
                    "error": (body.error or "the run hit its turn ceiling")
                    + f"\n\nResume attempts exhausted ({attempts}/{cap}) — "
                    "landing as failed rather than pausing again."
                }
            )

        # US-33.2: a run the gateway stopped at its ceiling did not fail — it
        # was stopped, and it says so with the number it hit. Landing it as a
        # generic failure would feed the repair loop and us-33.4's escalation a
        # wrong premise, which is the mistake us-27.12 was written about. The
        # marker was written by the gateway while the run was still running.
        stopped_reason = run.get("stopped_reason")
        outcome = "stopped" if stopped_reason else "failed"
        error_text = stopped_reason or body.error
        # US-33.4: store the classification, which us-10.11 has always sent and
        # nothing has ever kept. It is what decides whether the next attempt
        # escalates or repeats.
        try:
            db.record_run_fault_class(settings, run_id, body.fault_class)
        except Exception:  # noqa: BLE001 — never fail a hand-back over a label
            logger.warning("could not record the fault class for %s", run_id)
        # failure report — the issue returns to 'failed' for re-dispatch,
        # and a synced GitHub issue closes (US-1.20), as the old callback did
        try:
            ok = db.complete_run(
                settings,
                run_id,
                outcome,
                body.stdout,
                None,
                body.branch_ref,
                None,
                error_text,
                worker_name=worker["name"],
                trigger=trigger,
                claude_session_id=body.claude_session_id,
                **usage,
            )
        except HTTPException:
            raise
        except Exception:
            # US-31.1: a failure report must never be lost to its own
            # bookkeeping. On 2026-07-26 this branch 500'd twice; both runs
            # sat `running` until their leases looped them, four times over.
            # Whatever complete_run tripped on, record the primary fact —
            # the run failed — through the minimal path, and log the real
            # traceback so the next reader has what this incident did not.
            logger.exception(
                "complete_run raised on a failure report for run %s; "
                "recording the failure via the minimal path",
                run_id,
            )
            ok = db.fail_run_minimal(
                settings, run_id, error_text, worker_name=worker["name"]
            )
        if not ok:
            raise HTTPException(
                status_code=409, detail="run is not claimed — claim it and retry"
            )
        await issue_sync.push_issue_state_via_db(
            settings, str(run["issue_id"]), "closed"
        )
        # US-10.11: a runner-fault is the machine's problem, not the story's —
        # record it and ping the org's managers so a broken runner is fixable.
        if body.fault_class == "runner-fault":
            try:
                db.record_runner_incident(
                    settings,
                    str(run["org_id"]),
                    str(worker["id"]),
                    run_id,
                    "runner-fault",
                    body.error,
                )
                db.notify_org_managers(
                    settings,
                    str(run["org_id"]),
                    "runner_fault",
                    {
                        "worker": worker.get("name"),
                        "run_id": str(run_id),
                        "message": (body.error or "")[:200],
                    },
                )
            except Exception:  # noqa: BLE001 — never fail the submit on telemetry
                logger.warning("runner incident recording failed for run %s", run_id)
        return {"ok": True, "issue_status": "failed"}

    if run["kind"] == "prd":
        if not body.prd:
            raise HTTPException(status_code=422, detail="prd markdown is required")
        # US-5.21: structural findings ride the response as warnings —
        # signal for the worker loop, never a new rejection path.
        warnings = validation.validate_prd(body.prd)
        ok = db.complete_run(
            settings,
            run_id,
            "succeeded",
            body.stdout,
            None,
            None,
            None,
            None,
            prd=body.prd,
            worker_name=worker["name"],
            trigger=trigger,
            claude_session_id=body.claude_session_id,
            **usage,
        )
        if not ok:
            raise HTTPException(
                status_code=409, detail="run is not claimed — claim it and retry"
            )
        _store_handback_notes(settings, run, worker, run_id, body.notes)
        out = {"ok": True, "issue_status": "prd-review"}
        if warnings:
            out["warnings"] = warnings
        auto = await _maybe_auto_approve(settings, run, body)
        if auto:
            out["auto_approved"] = auto
        return out

    if run["kind"] == "breakdown":
        # US-2.33: the worker hands back the proposed split; the factory
        # auto-creates the child stories as drafts (db.complete_run) and the
        # feature returns to 'ready' as a container for the manager to curate.
        if not body.stories:
            raise HTTPException(status_code=422, detail="stories are required")
        warnings = validation.validate_stories(body.stories)
        ok = db.complete_run(
            settings,
            run_id,
            "succeeded",
            body.stdout,
            None,
            None,
            None,
            None,
            stories=body.stories,
            worker_name=worker["name"],
            trigger=trigger,
            claude_session_id=body.claude_session_id,
            **usage,
        )
        if not ok:
            raise HTTPException(
                status_code=409, detail="run is not claimed — claim it and retry"
            )
        # US-7.1: score the freshly created children off the critical path —
        # a scoring failure never fails the submit response.
        try:
            from .. import complexity

            for cid in db.child_story_ids(settings, str(run["issue_id"])):
                await complexity.score_and_store_issue(
                    settings, cid, basis="story"
                )
        except Exception:  # noqa: BLE001 — best-effort; scorer never raises
            logger.warning(
                "story complexity scoring failed for feature %s",
                run.get("issue_id"),
            )
        _store_handback_notes(settings, run, worker, run_id, body.notes)
        out = {"ok": True, "issue_status": "ready", "story_count": len(body.stories)}
        if warnings:
            out["warnings"] = warnings
        return out

    if run["kind"] == "plan":
        if not body.plan:
            raise HTTPException(status_code=422, detail="plan markdown is required")
        warnings = validation.validate_plan(body.plan, body.test_plan)
        ok = db.complete_run(
            settings,
            run_id,
            "succeeded",
            body.stdout,
            None,
            None,
            None,
            None,
            plan=body.plan,
            test_plan=body.test_plan,
            worker_name=worker["name"],
            trigger=trigger,
            claude_session_id=body.claude_session_id,
            **usage,
        )
        if not ok:
            raise HTTPException(
                status_code=409, detail="run is not claimed — claim it and retry"
            )
        if warnings:
            # US-5.21: the manager must see a plan whose test plan would
            # materialize nothing — the plan-review page reads this event.
            # Best-effort: the submit already succeeded.
            try:
                db.record_issue_event(
                    settings,
                    str(run["org_id"]),
                    str(run["issue_id"]),
                    "submission-findings",
                    {"run_id": str(run_id), "kind": "plan", "findings": warnings},
                )
            except Exception:
                logger.warning(
                    "could not record submission findings for run %s", run_id
                )
        # US-7.1: refine the complexity estimate from the freshly stored plan.
        # Off the critical path — never delays or fails the submit response.
        try:
            from .. import complexity

            await complexity.score_and_store_issue(
                settings, str(run["issue_id"]), basis="plan"
            )
        except Exception:  # noqa: BLE001 — best-effort; scorer never raises
            logger.warning(
                "complexity refine failed for issue %s", run.get("issue_id")
            )
        _store_handback_notes(settings, run, worker, run_id, body.notes)
        out = {"ok": True, "issue_status": "plan-review"}
        if warnings:
            out["warnings"] = warnings
        auto = await _maybe_auto_approve(settings, run, body)
        if auto:
            out["auto_approved"] = auto
        return out

    # code run: verify the branch via the GitHub App, open/adopt the PR,
    # and pull the diff from GitHub — worker-posted diffs are not trusted.
    # The one exception is the simulated provider (US-3.5 parity): a
    # simulated:// pr_url carries its own diff and skips verification.
    if body.pr_url:
        if not body.pr_url.startswith("simulated://"):
            raise HTTPException(
                status_code=422,
                detail="the factory opens PRs itself — submit the branch_ref",
            )
        ok = db.complete_run(
            settings,
            run_id,
            "succeeded",
            body.stdout,
            body.diff,
            body.branch_ref,
            body.pr_url,
            None,
            test_cases=[t.model_dump() for t in body.test_cases or []],
            worker_name=worker["name"],
            trigger=trigger,
            claude_session_id=body.claude_session_id,
            **usage,
        )
        if not ok:
            raise HTTPException(
                status_code=409, detail="run is not claimed — claim it and retry"
            )
        _store_handback_notes(settings, run, worker, run_id, body.notes)
        return {"ok": True, "pr_url": body.pr_url, "issue_status": "in-review"}

    if not body.branch_ref:
        raise HTTPException(status_code=422, detail="branch_ref is required")

    ic = run.get("input_context") or {}
    repo_full = ic.get("repo_full_name") or ""
    if "/" not in repo_full:
        raise HTTPException(status_code=422, detail="run has no linked repo")
    owner, repo = repo_full.split("/", 1)
    default_branch = run.get("default_branch") or ic.get("default_branch") or "main"
    # US-7.15: in `direct` (main-strategy) mode the work was committed to the
    # default branch — no PR to open, the review gate is bypassed.
    _branch, _strategy, submit_mode = db.resolve_working_branch(settings, run)
    direct = submit_mode == "direct"

    # US-5.24: credential problems are never worker-fixable — the detail
    # says what broke and the hint names who owns the fix, so "push the
    # branch first" is only ever said when pushing would actually help.
    try:
        token = await github_tokens.token_for_org(
            settings, str(run["org_id"]), repo_full
        )
    except github.GitHubError as e:
        exc = HTTPException(status_code=422, detail=e.message)
        exc.hint = (
            "only a manager can fix this — ask them to connect GitHub in "
            "Settings → GitHub"
            if isinstance(e, github.GitHubNotConfigured)
            else "only a manager can fix this — ask them to reconnect "
            "GitHub in Settings → GitHub"
        )
        raise exc

    try:
        await github.get_branch(token, owner, repo, body.branch_ref)
    except github.GitHubError as e:
        if "not found" in e.message:
            exc = HTTPException(
                status_code=422,
                detail=(
                    f"branch '{body.branch_ref}' not found on GitHub — "
                    "push it and retry"
                ),
            )
            exc.hint = "push the branch to the factory remote, then submit again"
        else:
            exc = HTTPException(status_code=502, detail=e.message)
            exc.hint = (
                "GitHub answered an unexpected error — retry, and if it "
                "persists ask the manager to check the GitHub connection"
            )
        raise exc

    pr_url: str | None = None
    diff: str | None = None
    if direct:
        # Committed straight to the default branch — no PR. The change is the
        # branch head itself; the diff surface is the commit, not a compare.
        pass
    else:
        try:
            for p in await github.list_open_pulls(token, owner, repo):
                if p.get("head", {}).get("ref") == body.branch_ref:
                    pr_url = p.get("html_url")
                    break
            if not pr_url:
                title = run.get("issue_title") or ic.get("title") or body.branch_ref
                pr = await github.create_pull(
                    token,
                    owner,
                    repo,
                    body.branch_ref,
                    default_branch,
                    title,
                    body.notes or f"Factory work item {run['issue_id']}",
                )
                pr_url = pr.get("html_url")
        except github.GitHubError as e:
            raise HTTPException(status_code=502, detail=f"GitHub: {e}")

        try:
            diff = await github.get_compare_diff(
                token, owner, repo, default_branch, body.branch_ref
            )
        except github.GitHubError:
            diff = None  # review falls back to the PR link

    # US-40.2: attribute the branch's commits to the stories they landed,
    # BEFORE the run is completed — `complete_run`'s fan-out reads the
    # coverage record to decide which stories move forward.
    #
    # `run_item_commits` was written only by the MCP `submit_changeset` tool,
    # so a git hand-back opened a PR carrying no coverage at all. The fan-out
    # then did exactly what us-27.1 built it to do — ask the record rather than
    # the agent — found nothing, and returned every story to the pool. On
    # 2026-07-28 that turned a successful six-story build into six stories back
    # at `planned` and a feature nobody could approve. The table had never held
    # a single row in production.
    # `direct` committed straight to the default branch, so there is no
    # branch to compare and nothing to attribute — don't even ask for the
    # membership. (Multi-story attribution in direct mode is out of scope:
    # see us-40.2. It behaves as it did before this story.)
    members = [] if direct else db.run_members(settings, run_id)
    if len(members) > 1:
        await _attribute_branch_coverage(
            settings,
            token,
            owner,
            repo,
            default_branch,
            body.branch_ref,
            run_id,
            str(run["org_id"]),
            members,
        )
        # 2026-08-13 (FEAT-2.8): the gate used the walk's RETURN — a count of
        # newly written rows — as the coverage verdict. record_changeset_
        # coverage is idempotent (`on conflict do nothing`), so an MCP run
        # whose rows were all recorded at apply time walked to ZERO and a
        # fully attributed branch answered "carries no story attribution",
        # forever; the agent re-attributed eleven stories in a loop it could
        # not win. The verdict now asks the RECORD — us-27.1's own principle —
        # which both transports write: the walk seeds it for git-native
        # hand-backs, apply_changeset already wrote it for MCP ones.
        covered = any(
            m.get("landed") for m in db.run_members(settings, run_id)
        )
        if not covered:
            # Refused rather than accepted-and-emptied. The same shape as the
            # branch-not-found refusal above: the agent can add trailers and
            # submit again, and the run stays claimed meanwhile. Reporting
            # success here is what made the incident silent.
            exc = HTTPException(
                status_code=422,
                detail=(
                    f"branch '{body.branch_ref}' carries no story attribution "
                    f"— this run covers {len(members)} stories and nothing on "
                    "the branch says which of them your commits landed"
                ),
            )
            exc.hint = (
                "add a 'Factory-Story: <id>' trailer to each commit message "
                "naming the story that commit implements ("
                + ", ".join(str(m["display_id"]) for m in members)
                + "), then push and submit again"
            )
            raise exc

    ok = db.complete_run(
        settings,
        run_id,
        "succeeded",
        body.stdout,
        diff,
        body.branch_ref,
        pr_url,
        None,
        test_cases=[t.model_dump() for t in body.test_cases or []],
        worker_name=worker["name"],
        trigger=trigger,
        direct=direct,
        claude_session_id=body.claude_session_id,
        **usage,
    )
    if not ok:
        raise HTTPException(
            status_code=409, detail="run is not claimed — claim it and retry"
        )
    _store_handback_notes(settings, run, worker, run_id, body.notes)
    out = {
        "ok": True,
        "pr_url": pr_url,
        "issue_status": "merged" if direct else "in-review",
    }
    if direct:
        out["landed"] = "direct"  # committed to the default branch, no PR
    # US-5.21: an empty diff means nothing to merge — warn, don't block.
    if diff is not None and not diff.strip():
        out["warnings"] = [
            f"branch '{body.branch_ref}' has no changes beyond "
            f"'{default_branch}' — the PR diff is empty"
        ]
    # US-17.4: a `direct` landing already merged; only a PR-in-review can be
    # auto-approved (which merges it). The token/owner/repo/pr_url are in scope.
    if not direct:
        auto = await _maybe_auto_approve(
            settings, run, body, merge_ctx=(token, owner, repo, pr_url)
        )
        if auto:
            out["auto_approved"] = auto
            if auto.get("merged") == "merged":
                out["issue_status"] = "merged"
    return out


@router.post("/runs/{run_id}/submit")
async def submit(
    run_id: str,
    body: Submit,
    worker: dict = Depends(verify_worker),
    settings: Settings = Depends(get_settings),
):
    return await perform_submit(settings, worker, run_id, body)


@router.post("/runs/{run_id}/release")
async def release(
    run_id: str,
    body: Release,
    worker: dict = Depends(verify_worker),
    settings: Settings = Depends(get_settings),
):
    if not db.release_claim(settings, run_id, worker, note=body.note):
        raise HTTPException(
            status_code=409, detail="no live claim on this run to release"
        )
    return {"ok": True}


# ------------------------------- release prep (US-63.3) -------------------
#
# Deliberately not on the /runs/* surface: release prep is not a story-shaped
# run (no issue, no attempt budget, no lease/resume machinery) — it has its
# own table and its own three-endpoint contract, identical here and on the
# MCP tools in factory_mcp.py (list_release_prep_work/claim_release_prep_work/
# submit_release_notes), which both call straight into release_prep.py so
# neither transport can diverge on what "done" requires.


@router.get("/release-prep")
async def list_release_prep(
    worker: dict = Depends(verify_worker),
    settings: Settings = Depends(get_settings),
):
    rows = db.list_release_prep_pool(settings, str(worker["org_id"]))
    return {"items": rows}


# Declared BEFORE /release-prep/{prep_id}: FastAPI matches in order, and a
# literal path registered after a parameterised one is never reached.
@router.get("/release-prep/held")
async def list_held_release_prep(
    worker: dict = Depends(verify_worker),
    settings: Settings = Depends(get_settings),
):
    """US-103.2: the release preps this worker is already holding.

    A runner that restarts mid-prep asks this at startup and re-adopts what it
    finds, so a routine restart costs a minute rather than a release. Scoped
    to the caller's own worker id and org — it never reports another worker's
    claim, and a reaped or stopped prep is no longer `running`, so it is never
    returned and never resumed.
    """
    rows = db.list_held_release_preps(
        settings, str(worker["id"]), str(worker["org_id"])
    )
    return {
        "items": [
            {
                "id": str(r["id"]),
                "release_id": str(r["release_id"]),
                "project_id": str(r["project_id"]),
                "project_name": r["project_name"],
                "repo_full_name": r["repo_full_name"],
                "version": r["version"],
                "commit_sha": r["commit_sha"],
                "claimed_at": _iso(r["claimed_at"]),
                "claim_expires_at": _iso(r["claim_expires_at"]),
                # The identical briefing a fresh claim hands back, so
                # re-adoption runs the job exactly as claiming it would —
                # read live, so a restart picks up today's instruction.
                **release_prep.briefing(settings, str(r["id"]), worker),
            }
            for r in rows
        ]
    }


@router.get("/release-prep/{prep_id}")
async def get_release_prep_status(
    prep_id: str,
    worker: dict = Depends(verify_worker),
    settings: Settings = Depends(get_settings),
):
    """US-63.x follow-up: lets the runner verify a claimed prep actually
    reached 'succeeded'/'failed' rather than trusting a clean CLI exit — an
    agent that exits 0 without calling submit_release_notes must not read as
    done."""
    row = db.get_release_prep(settings, prep_id, str(worker["org_id"]))
    if not row:
        raise HTTPException(status_code=404, detail="release prep not found")
    return {"id": str(row["id"]), "status": row["status"]}


@router.post("/release-prep/{prep_id}/claim")
async def claim_release_prep(
    prep_id: str,
    worker: dict = Depends(verify_worker),
    settings: Settings = Depends(get_settings),
):
    result = await release_prep.claim(settings, prep_id, worker)
    if "error" in result:
        raise HTTPException(status_code=409, detail=result["error"])
    return result


@router.post("/release-prep/{prep_id}/heartbeat")
async def heartbeat_release_prep(
    prep_id: str,
    worker: dict = Depends(verify_worker),
    settings: Settings = Depends(get_settings),
):
    if not db.heartbeat_release_prep(settings, prep_id, str(worker["id"])):
        # us-103.3: an agent whose release was stopped under it learns why
        # here, on its next beat, rather than reading "no live claim".
        row = db.get_release_prep(settings, prep_id, str(worker["org_id"]))
        raise HTTPException(
            status_code=409,
            detail=(
                release_prep.not_running_error(row["status"])
                if row and row["status"] != "running"
                else "no live claim to extend"
            ),
        )
    return {"ok": True}


class ReleasePrepSubmit(BaseModel):
    @field_validator("*", mode="before")
    @classmethod
    def _no_nul_bytes(cls, v: Any) -> Any:
        return _strip_nul(v)

    notes_summary: str
    notes_detail: str
    # us-101.2: `dict[str, str]` could not carry `critical` (a bool) or `sort`
    # (an int), and pydantic would have coerced or 422'd them at the door.
    test_cases: list[dict[str, Any]] | None = None
    error: str | None = None
    # us-100.6 added these to the MCP tool and NOT here, so a git-native
    # worker submitting over HTTP could not propose a version at all. The
    # module docstring below says both transports call the same functions;
    # that only holds if both transports can express the same call.
    proposed_version: str | None = None
    version_rationale: str | None = None
    notes_doc: dict[str, Any] | None = None
    uncovered: list[str] | None = None


@router.post("/release-prep/{prep_id}/submit")
async def submit_release_prep(
    prep_id: str,
    body: ReleasePrepSubmit,
    worker: dict = Depends(verify_worker),
    settings: Settings = Depends(get_settings),
):
    if body.error:
        row = db.fail_release_prep(settings, prep_id, body.error)
        if not row:
            raise HTTPException(status_code=409, detail="release prep is not claimed")
        return {"ok": True, "run_status": "failed"}
    result = await release_prep.submit(
        settings,
        prep_id,
        worker,
        body.notes_summary,
        body.notes_detail,
        body.test_cases,
        proposed_version=(body.proposed_version or "").strip() or None,
        version_rationale=(body.version_rationale or "").strip() or None,
        notes_doc=body.notes_doc,
        uncovered=body.uncovered,
    )
    if "error" in result:
        raise HTTPException(status_code=422, detail=result["error"])
    return result


# ------------------------------- documents (US-2.21/2.22, moved in US-3.5)


def _owned_active_run(
    settings: Settings, run_id: str, worker: dict[str, Any]
) -> dict[str, Any]:
    """The run for a document call: must exist in the worker's org and be
    held by the caller — cross-org and foreign claims answer 404, never
    an existence leak."""
    run = db.get_run_for_documents(settings, run_id)
    if not run or str(run["org_id"]) != str(worker["org_id"]):
        raise HTTPException(status_code=404, detail="run not found")
    if str(run.get("worker_id") or "") != str(worker["id"]):
        raise HTTPException(status_code=404, detail="run not found")
    return run


@router.post("/runs/{run_id}/documents", status_code=201)
async def upload_run_document(
    run_id: str,
    file: UploadFile = File(...),
    worker: dict = Depends(verify_worker),
    settings: Settings = Depends(get_settings),
):
    """Agent upload mid-run: the document lands attached to the run's
    work item (source = 'agent'). Same filename replaces in place."""
    run = _owned_active_run(settings, run_id, worker)
    if run["status"] != "running":
        raise HTTPException(status_code=409, detail="run is not active")

    content = await file.read()
    if len(content) > documents.MAX_DOCUMENT_BYTES:
        raise HTTPException(status_code=413, detail="document exceeds 25 MB")
    if not content:
        raise HTTPException(status_code=422, detail="empty file")

    doc = await documents.create_or_replace(
        settings,
        org_id=str(run["org_id"]),
        project_id=str(run["project_id"]),
        name=file.filename or "document",
        content=content,
        mime_type=file.content_type,
        source="agent",
        attached_to="work-item",
        issue_id=str(run["issue_id"]),
        run_id=run_id,
    )
    return {"document": doc}


@router.get("/runs/{run_id}/documents/{document_id}")
async def fetch_run_document(
    run_id: str,
    document_id: str,
    worker: dict = Depends(verify_worker),
    settings: Settings = Depends(get_settings),
):
    """Byte-fetch for the context bundle's `documents` entries: the run's
    own work-item attachments plus its governing PRD's documents
    (US-2.22). Anything else is a 404 — never leak existence."""
    run = _owned_active_run(settings, run_id, worker)
    doc = documents.get_document(settings, document_id)
    if not doc or str(doc["org_id"]) != str(run["org_id"]):
        raise HTTPException(status_code=404, detail="document not found")

    is_work_item_doc = (
        doc["attached_to"] == "work-item"
        and str(doc.get("issue_id")) == str(run["issue_id"])
    )
    is_prd_doc = (
        doc["attached_to"] == "prd"
        and run.get("prd_issue_id") is not None
        and str(doc.get("issue_id")) == str(run["prd_issue_id"])
    )
    if not (is_work_item_doc or is_prd_doc):
        raise HTTPException(status_code=404, detail="document not found")

    data = await documents.read_bytes(settings, doc)
    if data is None:
        raise HTTPException(status_code=404, detail="document not found")
    return Response(
        content=data,
        media_type=doc["mime_type"],
        headers={"Content-Disposition": f'attachment; filename="{doc["name"]}"'},
    )
