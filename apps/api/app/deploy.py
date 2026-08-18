"""Deployment run pipeline (US-1.32).

`api` orchestrates the whole run server-side: resolve the branch head via
the GitHub App, download that commit's tarball, transfer + extract it into
the target folder over the SSH/SFTP bridge, then execute the deployment
script over SSH exec. Progress lands in append-only deployment_run_events
(Realtime pushes it to the browser); the captured script output lands in
deployment_runs.log.

All writes here use direct Postgres (service role equivalent) because the
pipeline outlives the triggering request — a user JWT could expire mid-run.
Authorization happened in the router: the run row only exists because the
caller could see the deployment under their own JWT (RLS).
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import os
import posixpath
import shlex
import tarfile
import tempfile
import time
from typing import Any

import paramiko
import psycopg

logger = logging.getLogger("uvicorn.error")

from . import github, github_tokens, notify, ssh, storage
from .config import Settings
from .pool import pool_for

# Keep strong references to in-flight pipeline tasks (asyncio only holds
# weak ones); a task removes itself when done.
_TASKS: set[asyncio.Task] = set()

# Live pipelines by run id, so a cancel request can reach them (US-1.35).
_RUNNING: dict[str, asyncio.Task] = {}

UPLOAD_EVENT_INTERVAL_SECONDS = 2.0
LOG_FLUSH_EVERY_LINES = 25


class RunActive(Exception):
    """Single-flight: this deployment already has a queued/running run."""


class PipelineError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _connect(settings: Settings):
    """US-87.6: leased from the process-wide pool (app/pool.py), not a new
    connection per call. Shares one pool with db.py so the whole API has a
    single, bounded connection budget."""
    return pool_for(settings).connection()


# ---------------------------------------------------------------------------
# Run records (sync, called via to_thread)
# ---------------------------------------------------------------------------


def create_run(
    settings: Settings,
    deployment: dict[str, Any],
    started_by: str,
    started_by_email: str,
    source: str = "branch",
    zip_filename: str | None = None,
    branch_override: str | None = None,
    release_id: str | None = None,
) -> str:
    """Insert a queued run; RunActive if single-flight blocks it.

    US-63.2: release_id, when set, is read back by run_pipeline's own
    terminal points to update the release that triggered this run — deploy.py
    otherwise knows nothing about releases."""
    try:
        with _connect(settings) as conn:
            row = conn.execute(
                """
                insert into public.deployment_runs
                  (org_id, deployment_id, source, branch, zip_filename,
                   is_override, started_by, started_by_email, release_id)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                returning id
                """,
                (
                    deployment["org_id"],
                    deployment["id"],
                    source,
                    (
                        branch_override
                        if branch_override
                        else deployment["branch"] if source == "branch" else None
                    ),
                    zip_filename,
                    branch_override is not None,
                    started_by,
                    started_by_email,
                    release_id,
                ),
            ).fetchone()
            conn.commit()
            return str(row["id"])
    except psycopg.errors.UniqueViolation:
        raise RunActive()




ARCHIVE_MAX_BYTES = 500 * 1024 * 1024  # per-artifact limit, stated in the UI


def create_derived_run(
    settings: Settings,
    deployment: dict[str, Any],
    source_run: dict[str, Any],
    relation: str,  # 'redeploy' (US-1.47) or 'promote' (US-1.43)
    started_by: str,
    started_by_email: str,
) -> str:
    """A new run pinned to another run's payload, with provenance."""
    col = "redeploy_of_run_id" if relation == "redeploy" else "promoted_from_run_id"
    try:
        with _connect(settings) as conn:
            row = conn.execute(
                f"""
                insert into public.deployment_runs
                  (org_id, deployment_id, source, branch, commit_sha,
                   commit_message, zip_filename, {col},
                   started_by, started_by_email)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                returning id
                """,  # noqa: S608 — col is one of two fixed names
                (
                    deployment["org_id"],
                    deployment["id"],
                    source_run["source"],
                    source_run.get("branch"),
                    source_run.get("commit_sha"),
                    source_run.get("commit_message"),
                    source_run.get("zip_filename"),
                    str(source_run["id"]),
                    started_by,
                    started_by_email,
                ),
            ).fetchone()
            conn.commit()
            return str(row["id"])
    except psycopg.errors.UniqueViolation:
        raise RunActive()


def map_commits_to_issues(
    settings: Settings, project_id: str, shas: list[str]
) -> dict[str, dict[str, str]]:
    """US-1.48: which factory issue shipped each commit (by merge SHA)."""
    if not shas:
        return {}
    with _connect(settings) as conn:
        rows = conn.execute(
            """
            select r.merge_commit_sha as sha, i.id, i.title
            from public.runs r
            join public.issues i on i.id = r.issue_id
            where i.project_id = %s and r.merge_commit_sha = any(%s)
            """,
            (project_id, shas),
        ).fetchall()
    return {r["sha"]: {"id": str(r["id"]), "title": r["title"]} for r in rows}


def set_run_merge_sha(settings: Settings, run_id: str, sha: str) -> None:
    """US-1.48 backfill: persist a merge SHA resolved from a stored pr_url."""
    with _connect(settings) as conn:
        conn.execute(
            "update public.runs set merge_commit_sha = %s"
            " where id = %s and merge_commit_sha is null",
            (sha, run_id),
        )
        conn.commit()


def has_active_run(settings: Settings, deployment_id: str) -> bool:
    with _connect(settings) as conn:
        row = conn.execute(
            "select 1 from public.deployment_runs"
            " where deployment_id = %s and status in ('queued', 'running') limit 1",
            (deployment_id,),
        ).fetchone()
    return row is not None


def update_staged_zip(
    settings: Settings,
    deployment_id: str,
    filename: str,
    size_bytes: int,
    sha256: str,
    uploaded_by_email: str,
) -> None:
    with _connect(settings) as conn:
        conn.execute(
            """
            update public.deployments
            set staged_zip_filename = %s, staged_zip_bytes = %s,
                staged_zip_sha256 = %s, staged_zip_uploaded_by_email = %s,
                staged_zip_uploaded_at = now()
            where id = %s
            """,
            (filename, size_bytes, sha256, uploaded_by_email, deployment_id),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Branch payload filtering (US-1.36)
# ---------------------------------------------------------------------------


def matches_exclude(rel_path: str, patterns: list[str]) -> bool:
    """gitignore-style, pragmatically: 'dir/' excludes a directory anywhere,
    a pattern with '/' matches the relative path, a bare pattern matches
    the basename."""
    base = posixpath.basename(rel_path)
    for p in patterns:
        p = p.strip()
        if not p or p.startswith("#"):
            continue
        if p.endswith("/"):
            d = p.strip("/")
            if rel_path == d or rel_path.startswith(d + "/") or f"/{d}/" in f"/{rel_path}":
                return True
        elif "/" in p:
            if fnmatch.fnmatch(rel_path, p.lstrip("/")):
                return True
        elif fnmatch.fnmatch(base, p):
            return True
    return False


def filter_tarball(
    src_path: str, dest_path: str, source_folder: str, exclude_patterns: str
) -> int:
    """Rewrite a GitHub tarball keeping only source_folder's contents minus
    excludes (US-1.36). Members are re-rooted under 'payload/' so extraction
    keeps using --strip-components=1. Returns the number of files kept."""
    sub = source_folder.strip().strip("/")
    patterns = [l for l in (exclude_patterns or "").splitlines() if l.strip()]
    kept = 0
    with tarfile.open(src_path, "r:gz") as src, tarfile.open(dest_path, "w:gz") as dst:
        for member in src:
            parts = member.name.split("/", 1)
            if len(parts) < 2 or not parts[1]:
                continue  # the wrapper directory itself
            rel = parts[1]
            if sub:
                if rel == sub:
                    continue
                if not rel.startswith(sub + "/"):
                    continue
                rel = rel[len(sub) + 1 :]
                if not rel:
                    continue
            if matches_exclude(rel, patterns):
                continue
            member.name = f"payload/{rel}"
            if member.isreg():
                dst.addfile(member, src.extractfile(member))
                kept += 1
            else:
                dst.addfile(member)
    return kept


def record_event(
    settings: Settings,
    org_id: str,
    run_id: str,
    phase: str,
    message: str,
    data: dict[str, Any] | None = None,
) -> None:
    with _connect(settings) as conn:
        conn.execute(
            """
            insert into public.deployment_run_events
              (org_id, run_id, phase, message, data)
            values (%s, %s, %s, %s, %s)
            """,
            (org_id, run_id, phase, message, json.dumps(data or {})),
        )
        conn.commit()


def _update_run(settings: Settings, run_id: str, fields: dict[str, Any]) -> None:
    sets = ", ".join(f"{k} = %s" for k in fields)
    with _connect(settings) as conn:
        conn.execute(
            f"update public.deployment_runs set {sets} where id = %s",  # noqa: S608
            (*fields.values(), run_id),
        )
        conn.commit()


REAPED_NOTE = "interrupted by API server restart"

# US-120.1: the sweep's grace beyond a deployment's own run_timeout_minutes
# before it declares a queued/running run with no live pipeline dead. The
# pipeline's own wait_for fires first whenever the process is alive; this
# only ever catches a run whose process is not.
STRANDED_GRACE_MINUTES = 5


def reap_orphaned_runs(settings: Settings) -> int:
    """Fail runs stranded by an api crash/restart (US-1.32).

    Called on startup: nothing can legitimately be queued/running before
    this process existed, so anything live is an orphan. Releases the
    single-flight lock by moving the run to a terminal status.

    US-120.1: a reaped run that a release fired (`release_id` set) settles
    the release too. Before this, the row went `failed` and the release
    stayed `deploying` — `_settle_release_deploy` lived only inside
    `run_pipeline`'s terminal branches, which a restart skips — and from
    `deploying` there was no legal move (2026.08.18.2, nine and a half
    hours, the project frozen by migrations 215/275).
    """
    with _connect(settings) as conn:
        rows = conn.execute(
            """
            update public.deployment_runs
            set status = 'failed', finished_at = now(),
                log = coalesce(log, '') || %s
            where status in ('queued', 'running')
            returning id, org_id, release_id
            """,
            (f"\n[{REAPED_NOTE}]",),
        ).fetchall()
        for row in rows:
            conn.execute(
                """
                insert into public.deployment_run_events
                  (org_id, run_id, phase, message)
                values (%s, %s, 'error', 'Run interrupted by API server restart')
                """,
                (row["org_id"], row["id"]),
            )
        conn.commit()
    for row in rows:
        if row.get("release_id"):
            _settle_release_deploy(
                settings,
                str(row["id"]),
                "failed",
                f"the UAT deploy was {REAPED_NOTE}",
            )
    return len(rows)


def settle_stranded_release_deploys(settings: Settings) -> list[dict[str, Any]]:
    """US-120.1: re-read every `deploying` release from its own deploy run.

    The reaper and the pipeline settle the release on the paths they own;
    this is the belt to those braces, run at startup and on the 60-second
    liveness loop. It exists because a release can be stranded by a path
    that never reaches either — the reaper skipped at startup because the
    database was unreachable, a settle swallowed by a DB hiccup, a second
    API process sharing the database, or the old code — and because
    2026.08.18.2 was already stranded on prod when this was written and had
    to be repaired by the code rather than by a hand in the database.

    Per `deploying` release, by its `uat_deployment_run_id` run:
      - missing (no id, or the row is gone)   → uat-deploy-failed
      - failed / cancelled                     → uat-deploy-failed
      - succeeded                              → uat-deployed
      - queued / running, NOT live in this process's `_RUNNING`, and older
        than the deployment's run_timeout_minutes (default 30) + grace
                                               → the run is failed, then
                                                 uat-deploy-failed
      - queued / running otherwise             → left alone: a live task
        owns its own wait_for; a young one may still be starting.
    Returns one dict per release it changed.
    """
    with _connect(settings) as conn:
        rows = conn.execute(
            """
            select r.id as release_id, r.version, r.uat_deployment_run_id,
                   dr.id as run_id, dr.org_id, dr.status as run_status,
                   dr.created_at as run_created_at, dr.finished_at as run_finished_at,
                   dr.log as run_log,
                   coalesce(d.run_timeout_minutes, 30) as timeout_minutes,
                   extract(epoch from (now() - dr.created_at)) / 60 as run_age_minutes
            from public.releases r
            left join public.deployment_runs dr on dr.id = r.uat_deployment_run_id
            left join public.deployments d on d.id = dr.deployment_id
            where r.status = 'deploying'
            """
        ).fetchall()

    settled: list[dict[str, Any]] = []
    for row in rows:
        release_id = str(row["release_id"])
        run_id = str(row["run_id"]) if row.get("run_id") else None
        run_status = row.get("run_status")
        landed: str | None = None
        reason: str | None = None

        if run_id is None:
            landed = "uat-deploy-failed"
            reason = "the UAT deploy run no longer exists"
        elif run_status in ("failed", "cancelled"):
            landed = "uat-deploy-failed"
            reason = f"the UAT deploy {run_status}" + _last_log_line(row.get("run_log"))
        elif run_status == "succeeded":
            landed = "uat-deployed"
        elif run_status in ("queued", "running"):
            task = _RUNNING.get(run_id)
            if task is not None and not task.done():
                continue  # live here; its own wait_for owns it
            age = float(row.get("run_age_minutes") or 0)
            limit = int(row.get("timeout_minutes") or 30) + STRANDED_GRACE_MINUTES
            if age < limit:
                continue
            note = (
                f"timed out: no pipeline reported for {int(age)} minutes "
                f"(limit {int(row.get('timeout_minutes') or 30)}); the API "
                "restarted or the run was lost"
            )
            with _connect(settings) as conn:
                conn.execute(
                    """
                    update public.deployment_runs
                    set status = 'failed', finished_at = now(),
                        log = coalesce(log, '') || %s
                    where id = %s and status in ('queued', 'running')
                    """,
                    (f"\n[{note}]", run_id),
                )
                conn.execute(
                    """
                    insert into public.deployment_run_events
                      (org_id, run_id, phase, message)
                    values (%s, %s, 'error', %s)
                    """,
                    (row["org_id"], run_id, "Run " + note),
                )
                conn.commit()
            landed = "uat-deploy-failed"
            reason = f"the UAT deploy {note}"
        else:
            continue  # an unknown status is not ours to interpret

        if landed == "uat-deployed":
            _settle_release_status(
                settings, release_id, "uat-deployed", None,
                deployed_at=row.get("run_finished_at"),
            )
        else:
            _settle_release_status(settings, release_id, landed, reason)
        settled.append(
            {
                "release_id": release_id,
                "version": row.get("version"),
                "run_id": run_id,
                "from_run_status": run_status,
                "landed": landed,
                "reason": reason,
            }
        )
    return settled


def _last_log_line(log: str | None) -> str:
    """The pipeline's own last word on a run, for a release's failure_reason:
    ' — [interrupted by API server restart]' or ''."""
    if not log:
        return ""
    lines = [l.strip() for l in str(log).splitlines() if l.strip()]
    return f" — {lines[-1][:300]}" if lines else ""


def upsert_env_var(
    settings: Settings,
    org_id: str,
    deployment_id: str,
    name: str,
    actor: str = "api",
) -> None:
    """Record an env var NAME (the value lives only in the data bucket)."""
    with _connect(settings) as conn:
        conn.execute(
            """
            insert into public.deployment_env_vars (org_id, deployment_id, name)
            values (%s, %s, %s)
            on conflict (deployment_id, name) do update set updated_at = now()
            """,
            (org_id, deployment_id, name),
        )
        conn.commit()
    record_config_event(
        settings, org_id, deployment_id, actor, ["env"], {"name": name, "action": "set"}
    )


def delete_env_var(
    settings: Settings,
    deployment_id: str,
    name: str,
    org_id: str | None = None,
    actor: str = "api",
) -> None:
    with _connect(settings) as conn:
        row = conn.execute(
            "delete from public.deployment_env_vars"
            " where deployment_id = %s and name = %s returning org_id",
            (deployment_id, name),
        ).fetchone()
        conn.commit()
    resolved_org = org_id or (str(row["org_id"]) if row else None)
    if resolved_org:
        record_config_event(
            settings,
            resolved_org,
            deployment_id,
            actor,
            ["env"],
            {"name": name, "action": "removed"},
        )


def list_env_var_names(settings: Settings, deployment_id: str) -> list[str]:
    with _connect(settings) as conn:
        rows = conn.execute(
            "select name from public.deployment_env_vars"
            " where deployment_id = %s order by name",
            (deployment_id,),
        ).fetchall()
    return [r["name"] for r in rows]


async def fetch_env_values(
    settings: Settings, org_id: str, deployment_id: str
) -> dict[str, str]:
    """Resolve the deployment's env vars from the data bucket (US-1.37).

    Values never leave this process except into the script's environment
    on the target server."""
    names = await asyncio.to_thread(list_env_var_names, settings, deployment_id)
    prefix = storage.deployment_prefix(org_id, deployment_id)
    values: dict[str, str] = {}
    for name in names:
        raw = await storage.get_object(settings, f"{prefix}/env/{name}")
        if raw is not None:
            values[name] = raw.decode("utf-8")
    return values


def make_masker(values: dict[str, str]):
    """Replace secret values with •••• in any outbound line (US-1.37).

    Best-effort by nature — transformed values can't be caught."""
    secrets = sorted(
        (v for v in values.values() if len(v) >= 3), key=len, reverse=True
    )
    if not secrets:
        return lambda line: line

    def mask(line: str) -> str:
        for s in secrets:
            if s in line:
                line = line.replace(s, "••••")
        return line

    return mask


def get_run(settings: Settings, run_id: str) -> dict[str, Any] | None:
    with _connect(settings) as conn:
        row = conn.execute(
            "select * from public.deployment_runs where id = %s", (run_id,)
        ).fetchone()
    return dict(row) if row else None


def create_rollback_run(
    settings: Settings,
    deployment: dict[str, Any],
    to_run: dict[str, Any],
    started_by: str,
    started_by_email: str,
) -> str:
    """Insert a rollback run entry (US-1.39): who/when/from->to, single-flight."""
    try:
        with _connect(settings) as conn:
            row = conn.execute(
                """
                insert into public.deployment_runs
                  (org_id, deployment_id, kind, source, branch, commit_sha,
                   release_path, rollback_to_run_id, started_by, started_by_email)
                values (%s, %s, 'rollback', %s, %s, %s, %s, %s, %s, %s)
                returning id
                """,
                (
                    deployment["org_id"],
                    deployment["id"],
                    to_run["source"],
                    to_run["branch"],
                    to_run["commit_sha"],
                    to_run["release_path"],
                    str(to_run["id"]),
                    started_by,
                    started_by_email,
                ),
            ).fetchone()
            conn.commit()
            return str(row["id"])
    except psycopg.errors.UniqueViolation:
        raise RunActive()


def record_config_event(
    settings: Settings,
    org_id: str,
    deployment_id: str,
    actor: str,
    areas: list[str],
    detail: dict[str, Any] | None = None,
) -> None:
    """US-1.49: api-side config writes (env vars) record their own event —
    the DB trigger can't see the real actor behind a service-role write."""
    with _connect(settings) as conn:
        conn.execute(
            """
            insert into public.deployment_events
              (org_id, deployment_id, actor, event, areas, detail)
            values (%s, %s, %s, 'updated', %s, %s)
            """,
            (org_id, deployment_id, actor, json.dumps(areas), json.dumps(detail or {})),
        )
        conn.commit()


def health_check_once(
    transport: paramiko.Transport, url: str, expected: int
) -> tuple[bool, str]:
    """One health probe, executed FROM the target server so internal and
    localhost URLs work (US-1.40)."""
    lines: list[str] = []
    status = _exec(
        transport,
        "command -v curl >/dev/null 2>&1 || { echo __NO_CURL__; exit 3; }; "
        f"curl -s -o /dev/null -w '%{{http_code}}' --max-time 10 {shlex.quote(url)}",
        None,
        lines.append,
    )
    if any("__NO_CURL__" in l for l in lines):
        raise PipelineError(
            "`curl` is not installed on this server (needed for the health check)."
        )
    code = lines[-1].strip() if lines else ""
    if status != 0 and not code:
        return False, f"curl exit {status}"
    return code == str(expected), f"HTTP {code}" if code else f"curl exit {status}"


def record_auto_rollback(
    settings: Settings,
    deployment: dict[str, Any],
    prev_release: str,
    failed_run_id: str,
) -> str:
    """US-1.40: bookkeep an auto-rollback exactly like a manual one — a
    rollback run entry plus the current_run_id repoint (US-1.34)."""
    with _connect(settings) as conn:
        prev = conn.execute(
            """
            select * from public.deployment_runs
            where deployment_id = %s and release_path = %s and status = 'succeeded'
            order by created_at desc limit 1
            """,
            (deployment["id"], prev_release),
        ).fetchone()
        failed = conn.execute(
            "select started_by from public.deployment_runs where id = %s",
            (failed_run_id,),
        ).fetchone()
        row = conn.execute(
            """
            insert into public.deployment_runs
              (org_id, deployment_id, kind, status, source, branch, commit_sha,
               zip_filename, release_path, rollback_to_run_id,
               started_by, started_by_email, started_at, finished_at, log)
            values (%s, %s, 'rollback', 'succeeded', %s, %s, %s, %s, %s, %s,
                    %s, %s, now(), now(), %s)
            returning id
            """,
            (
                deployment["org_id"],
                deployment["id"],
                prev["source"] if prev else "branch",
                prev["branch"] if prev else deployment.get("branch"),
                prev["commit_sha"] if prev else None,
                prev["zip_filename"] if prev else None,
                prev_release,
                str(prev["id"]) if prev else None,
                # started_by is not-null; prefer a real user id over the
                # org-id filler if the failed run's row is somehow gone.
                (failed or prev or {"started_by": deployment["org_id"]})["started_by"],
                "auto (failed health check)",
                f"[auto-rollback after failed health check: current -> {prev_release}]",
            ),
        ).fetchone()
        rb_id = str(row["id"])
        conn.execute(
            "update public.deployments set current_run_id = %s where id = %s",
            (rb_id, deployment["id"]),
        )
        conn.execute(
            """
            insert into public.deployment_run_events (org_id, run_id, phase, message)
            values (%s, %s, 'done', 'Auto-rollback after failed health check')
            """,
            (deployment["org_id"], rb_id),
        )
        conn.commit()
        return rb_id


def _settle_release_deploy(
    settings: Settings, run_id: str, outcome: str, reason: str | None = None
) -> None:
    """US-63.2: the last thing every terminal branch of both pipelines does.
    A no-op unless this run's deployment_runs.release_id is set (only true
    for a run launch_release_uat_deploy created) — deploy.py otherwise knows
    nothing about releases. Guarded to the still-deploying status so a stray
    late callback (already-cancelled release, etc.) never clobbers a state
    a human has since moved past.

    US-120.1: also called by every OTHER writer that ends a release-linked
    run — the startup reaper and request_cancel's no-live-task branch — and
    carries the writer's reason onto `releases.failure_reason`, which the
    release page renders and `retry` clears. `succeeded` writes no reason.

    Every exception is swallowed: this call sits inside each pipeline's
    already-terminal success/failure handling, so a DB hiccup here must never
    override the deployment's own true outcome — an ordinary (non-release)
    run has no release_id and would otherwise pay for this table even
    existing."""
    try:
        _settle_release_deploy_unsafe(settings, run_id, outcome, reason)
    except Exception:  # noqa: BLE001 — see docstring
        logger.warning("could not settle release status for run %s", run_id, exc_info=True)


def _settle_release_deploy_unsafe(
    settings: Settings, run_id: str, outcome: str, reason: str | None = None
) -> None:
    with _connect(settings) as conn:
        row = conn.execute(
            "select release_id from public.deployment_runs where id = %s",
            (run_id,),
        ).fetchone()
    if not row or not row["release_id"]:
        return
    if outcome == "succeeded":
        _settle_release_status(settings, str(row["release_id"]), "uat-deployed", None)
    else:
        _settle_release_status(
            settings, str(row["release_id"]), "uat-deploy-failed", reason
        )


def _settle_release_status(
    settings: Settings,
    release_id: str,
    status: str,
    reason: str | None,
    deployed_at: Any | None = None,
) -> bool:
    """The one UPDATE behind every deploy-side release settle (US-63.2,
    US-120.1). Guarded to `deploying`: whoever moved the release off it first
    wins, and a late writer is a no-op. Returns whether a row moved."""
    with _connect(settings) as conn:
        row = conn.execute(
            """
            update public.releases
            set status = %s,
                uat_deployed_at = case
                    when %s = 'uat-deployed' then coalesce(%s, now())
                    else uat_deployed_at end,
                failure_reason = case
                    when %s = 'uat-deployed' then failure_reason
                    else coalesce(left(%s, 500), failure_reason) end,
                updated_at = now()
            where id = %s and status = 'deploying'
            returning id
            """,
            (status, status, deployed_at, status, reason, release_id),
        ).fetchone()
        conn.commit()
    return row is not None


async def _launch_release_suites_if_any(settings: Settings, run_id: str) -> None:
    """US-81.3: the moment a release lands on UAT is the moment its automated
    suites run — same precedent as release-prep firing this deploy, no
    scheduler. A no-op for ordinary runs (no release_id). Late import because
    suites.py builds on this module; swallowed exceptions because the deploy's
    own outcome is already settled and must stand."""
    try:
        with _connect(settings) as conn:
            row = conn.execute(
                "select release_id from public.deployment_runs where id = %s",
                (run_id,),
            ).fetchone()
        if not row or not row["release_id"]:
            return
        from . import suites as suites_pipeline

        launched = await suites_pipeline.launch_release_suites(
            settings, str(row["release_id"])
        )
        if launched:
            logger.info(
                "release %s: launched %d suite run(s)", row["release_id"], launched
            )
    except Exception:  # noqa: BLE001 — see docstring
        logger.warning(
            "could not launch release suites for run %s", run_id, exc_info=True
        )


def _set_current_run(settings: Settings, deployment_id: str, run_id: str) -> None:
    """US-1.34: the durable 'what is live' pointer, set on every success."""
    with _connect(settings) as conn:
        conn.execute(
            "update public.deployments set current_run_id = %s where id = %s",
            (run_id, deployment_id),
        )
        conn.commit()


def _null_pruned_release(settings: Settings, deployment_id: str, release_path: str) -> None:
    """Retention pruned a release folder — reflect it on run rows so the UI
    shows those rollback targets as unavailable (US-1.39)."""
    with _connect(settings) as conn:
        conn.execute(
            "update public.deployment_runs set release_path = null"
            " where deployment_id = %s and release_path = %s",
            (deployment_id, release_path),
        )
        conn.commit()


def _persist_host_key(settings: Settings, server_id: str, fingerprint: str) -> None:
    with _connect(settings) as conn:
        conn.execute(
            "update public.servers set host_key_fingerprint = %s"
            " where id = %s and host_key_fingerprint is null",
            (fingerprint, server_id),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# SSH plumbing (sync, called via to_thread)
# ---------------------------------------------------------------------------


async def _resolve_credentials(settings: Settings, server: dict) -> ssh.Credentials:
    prefix = storage.server_prefix(server["org_id"], server["id"])
    if server["auth_method"] == "password":
        pw = await storage.get_object(settings, f"{prefix}/password")
        if pw is None:
            raise PipelineError("This server has no stored password.")
        return ssh.Credentials(password=pw.decode("utf-8"))
    key = await storage.get_object(settings, f"{prefix}/ssh_key")
    if key is None:
        raise PipelineError("This server has no stored SSH key.")
    passphrase = await storage.get_object(settings, f"{prefix}/ssh_key_passphrase")
    return ssh.Credentials(
        private_key=key.decode("utf-8"),
        passphrase=passphrase.decode("utf-8") if passphrase else None,
    )


def _upload(
    transport: paramiko.Transport,
    local_path: str,
    remote_path: str,
    progress_cb,
) -> None:
    sftp = paramiko.SFTPClient.from_transport(transport)
    if sftp is None:
        raise PipelineError("Could not open an SFTP channel.")
    sftp.put(local_path, remote_path, callback=progress_cb)
    sftp.close()


async def connect_to_server(settings: Settings, server: dict) -> ssh.Connection:
    """Open an authenticated transport (plain exceptions, TOFU persist)."""
    creds = await _resolve_credentials(settings, server)
    try:
        conn = await asyncio.to_thread(
            ssh.open_connection,
            host=server["host"],
            port=server["port"],
            username=server["username"],
            auth_method=server["auth_method"],
            creds=creds,
            expected_host_fingerprint=server.get("host_key_fingerprint"),
        )
    except ssh.HostKeyChanged as e:
        raise PipelineError(e.message)
    except ssh.SSHError as e:
        raise PipelineError(e.message)
    if not server.get("host_key_fingerprint"):
        await asyncio.to_thread(
            _persist_host_key, settings, server["id"], conn.host_key_fingerprint
        )
    return conn


PREFLIGHT_MIN_FREE_MB = 200  # sane floor when the payload size isn't known yet


def compute_required_free_mb(
    payload_bytes: int | None,
    strategy: str,
    keep_releases: int,
    floor_mb: int = PREFLIGHT_MIN_FREE_MB,
) -> tuple[int, str]:
    """Required free space for a deploy, and a human explanation (US-2.14).

    When the payload size is known (staged zip, archived artifact, promote
    source), we need the archive on disk plus its extracted copy — roughly
    2x — with a little headroom. For the `releases` strategy the new
    release lands alongside retained ones before the prune, so account for
    the retained releases too. Unknown sizes (branch deploys) fall back to
    the fixed floor, which is also the minimum for any known size."""
    if payload_bytes is None or payload_bytes <= 0:
        return floor_mb, f"unknown payload size — using the {floor_mb} MB floor"

    payload_mb = max(1, payload_bytes // 1_048_576)
    # archive + extracted copy, plus 20% headroom for temp files
    need = int(payload_mb * 2 * 1.2)
    why = f"payload {payload_mb} MB → archive + extracted copy"
    if strategy == "releases":
        # the extracted release sits beside the retained ones during the flip
        retained_mb = payload_mb * max(0, keep_releases)
        need += retained_mb
        why += f" + {keep_releases} retained releases"
    need = max(need, floor_mb)
    return need, f"{why} → need ≥ {need} MB"


def preflight_checks(
    transport: paramiko.Transport,
    target: str,
    min_free_mb: int = PREFLIGHT_MIN_FREE_MB,
    tools: tuple[str, ...] = ("tar",),
    space_reason: str | None = None,
) -> list[dict[str, Any]]:
    """Fast named checks on the target server (US-1.38). SSH connect+auth
    is implicitly check zero — this runs over an already-open transport.
    `min_free_mb` may be a payload-aware requirement (US-2.14); when it is,
    `space_reason` explains where the number came from."""
    results: list[dict[str, Any]] = [
        {"check": "ssh", "ok": True, "detail": "Connected and authenticated"}
    ]
    q_target = shlex.quote(target)

    lines: list[str] = []
    status = _exec(
        transport,
        f"mkdir -p {q_target} && touch {q_target}/.sf-preflight"
        f" && rm -f {q_target}/.sf-preflight",
        None,
        lines.append,
    )
    results.append(
        {
            "check": "target-writable",
            "ok": status == 0,
            "detail": (
                f"{target} exists (or was created) and is writable"
                if status == 0
                else f"Cannot create or write {target}: "
                + ("; ".join(lines[-2:]) or f"exit {status}")
            ),
        }
    )

    lines = []
    status = _exec(
        transport, f"df -Pk {q_target} | awk 'NR==2 {{print $4}}'", None, lines.append
    )
    free_kb = int(lines[0]) if status == 0 and lines and lines[0].isdigit() else None
    if free_kb is None:
        results.append(
            {"check": "disk-space", "ok": False, "detail": "Could not read free disk space (df)"}
        )
    else:
        free_mb = free_kb // 1024
        ok = free_kb >= min_free_mb * 1024
        basis = f" ({space_reason})" if space_reason else ""
        if ok:
            detail = f"{free_mb} MB free — need ≥ {min_free_mb} MB{basis}"
        else:
            short = min_free_mb - free_mb
            detail = (
                f"{free_mb} MB free — need ≥ {min_free_mb} MB{basis}; "
                f"short by {short} MB. Free space on the target or reduce the payload."
            )
        results.append({"check": "disk-space", "ok": ok, "detail": detail})

    for tool in tools:
        status = _exec(transport, f"command -v {shlex.quote(tool)} >/dev/null 2>&1")
        results.append(
            {
                "check": f"tool-{tool}",
                "ok": status == 0,
                "detail": f"`{tool}` is available"
                if status == 0
                else f"`{tool}` is not installed on this server",
            }
        )
    return results


def _exec(
    transport: paramiko.Transport,
    command: str,
    stdin_data: bytes | None = None,
    line_cb=None,
) -> int:
    """Run a command; stream combined stdout+stderr line-wise to line_cb."""
    chan = transport.open_session()
    chan.set_combine_stderr(True)
    chan.exec_command(command)
    if stdin_data is not None:
        chan.sendall(stdin_data)
    chan.shutdown_write()
    f = chan.makefile("rb")
    for raw in f:
        if line_cb:
            line_cb(raw.decode("utf-8", "replace").rstrip("\r\n"))
    status = chan.recv_exit_status()
    chan.close()
    return status


# ---------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------


def is_external(deployment: dict[str, Any] | None) -> bool:
    """US-50.1: the kind is a property of the deployment, defaulting to the
    one every existing row has."""
    return ((deployment or {}).get("kind") or "factory") == "external"


def launch(settings: Settings, ctx: dict[str, Any]) -> None:
    """Fire-and-forget the pipeline on the running event loop.

    US-50.2: this is the one place that learns a deployment's kind. Callers —
    the run endpoint, the promotion path, the agent's `trigger_deployment` —
    hand over the same ctx either way and never branch on it themselves.
    """
    if is_external(ctx.get("deployment")):
        coro = run_merge_pipeline(settings, ctx)
    else:
        coro = run_pipeline(settings, ctx)
    task = asyncio.get_running_loop().create_task(coro)
    _register(ctx["run_id"], task)


def launch_release_uat_deploy(
    settings: Settings,
    release: dict[str, Any],
    deployment: dict[str, Any],
    server: dict[str, Any] | None,
    project: dict[str, Any],
    actor_email: str,
) -> str:
    """US-63.2: fire the UAT deploy pipeline directly against a release's
    pinned commit, the moment its notes land — no agent, no manager click.
    Ships exactly what get_release_changes described: the pinned commit
    (release['commit_sha']), never the branch head at the time this runs.
    Returns the new deployment_runs id; deployment_runs.release_id (set via
    create_run) is what lets the pipeline's own terminal points
    (_settle_release_deploy) report back onto the release."""
    version = str(release["version"])
    sha = str(release["commit_sha"])
    # US-63.2: `deployment_runs.started_by` is a NOT NULL uuid — there is no
    # human actor for this trigger (it fires the moment release-prep
    # succeeds, no manager click), so the release's own `created_by` stands
    # in: the manager who cut the release is the reason this deploy exists,
    # even though an agent did the intermediate work. The literal "system"
    # this used to pass failed `invalid input syntax for type uuid` on
    # every single release, live on 2026-08-09.
    run_id = create_run(
        settings,
        deployment,
        str(release["created_by"]),
        actor_email or "system",
        "branch",
        None,
        sha,
        release_id=str(release["id"]),
    )
    launch(
        settings,
        {
            "run_id": run_id,
            "org_id": deployment["org_id"],
            "deployment": deployment,
            "server": server,
            "repo_full_name": project.get("repo_full_name") or "",
            "project_name": project.get("name") or "",
            "triggered_by": actor_email or "release",
            "override": {
                "ref": version,
                "sha": sha,
                "message": f"Release {version}",
            },
        },
    )
    return run_id


def _register(run_id: str, task: asyncio.Task) -> None:
    _TASKS.add(task)
    _RUNNING[run_id] = task

    def _cleanup(t: asyncio.Task) -> None:
        _TASKS.discard(t)
        if _RUNNING.get(run_id) is t:
            del _RUNNING[run_id]

    task.add_done_callback(_cleanup)


def request_cancel(
    settings: Settings, run_id: str, by: str, by_email: str
) -> str:
    """Cancel an active run (US-1.35). Returns 'signalled' when a live
    pipeline task was cancelled, 'marked' when only the row could be
    flipped (no live task in this process), 'not-active' otherwise."""
    with _connect(settings) as conn:
        row = conn.execute(
            """
            update public.deployment_runs
            set cancelled_by = %s, cancelled_by_email = %s
            where id = %s and status in ('queued', 'running')
            returning org_id
            """,
            (by, by_email, run_id),
        ).fetchone()
        conn.commit()
    if not row:
        return "not-active"

    record_event(
        settings, row["org_id"], run_id, "error", f"Cancellation requested by {by_email}"
    )
    task = _RUNNING.get(run_id)
    if task and not task.done():
        task.cancel()
        return "signalled"

    # No live task here (api restarted mid-run) — flip the row directly.
    with _connect(settings) as conn:
        conn.execute(
            """
            update public.deployment_runs
            set status = 'cancelled', finished_at = now(),
                log = coalesce(log, '') || E'\n[cancelled — no live pipeline; nothing to stop]'
            where id = %s and status in ('queued', 'running')
            """,
            (run_id,),
        )
        conn.commit()
    # US-120.1: this branch ends a run without the pipeline, so it settles
    # the release itself — the pipeline's CancelledError handler does it for
    # the signalled branch above.
    _settle_release_deploy(
        settings,
        run_id,
        "cancelled",
        f"the UAT deploy was cancelled by {by_email or 'the manager'}; "
        "no pipeline was running",
    )
    return "marked"


async def _shipped_commit_shas(
    settings: Settings, ctx: dict[str, Any], run_id: str
) -> list[str]:
    """US-2.9: every commit this deploy shipped — the range between the
    previous successful run's commit and this one (US-1.48 semantics).
    Falls back to just the deployed tip when GitHub can't answer."""

    def _endpoints() -> tuple[str | None, str | None]:
        with _connect(settings) as conn:
            cur = conn.execute(
                "select commit_sha from public.deployment_runs where id = %s",
                (run_id,),
            ).fetchone()
            prev = conn.execute(
                """
                select commit_sha from public.deployment_runs
                where deployment_id = %s and id <> %s
                  and status = 'succeeded' and commit_sha is not null
                order by finished_at desc nulls last
                limit 1
                """,
                (ctx["deployment"]["id"], run_id),
            ).fetchone()
        return (
            cur["commit_sha"] if cur else None,
            prev["commit_sha"] if prev else None,
        )

    current, previous = await asyncio.to_thread(_endpoints)
    if not current:
        return []  # zip deploys carry no commit identity
    if not previous or previous == current:
        return [current]
    try:
        owner, repo = (ctx.get("repo_full_name") or "").split("/", 1)
        token = await github_tokens.token_for_org(
            settings, str(ctx["org_id"]), ctx.get("repo_full_name")
        )
        cmp = await github.compare_commits(token, owner, repo, previous, current)
        shas = {c["sha"] for c in cmp.get("commits", [])}
        shas.add(current)
        return sorted(shas)
    except Exception:  # noqa: BLE001 — best-effort; the tip still matches
        return [current]


async def run_pipeline(settings: Settings, ctx: dict[str, Any]) -> None:
    """ctx: run_id, org_id, deployment (row dict), server (row dict),
    repo_full_name."""
    run_id: str = ctx["run_id"]
    org_id: str = ctx["org_id"]
    deployment: dict = ctx["deployment"]
    timeout_minutes: int = deployment.get("run_timeout_minutes") or 30

    state: dict[str, Any] = {"log": [], "unflushed": 0}

    def logline(line: str) -> None:
        state["log"].append(line)
        state["unflushed"] += 1

    def flush_log() -> None:
        _update_run(settings, run_id, {"log": "\n".join(state["log"])})
        state["unflushed"] = 0

    async def event(phase: str, message: str, data: dict | None = None) -> None:
        await asyncio.to_thread(
            record_event, settings, org_id, run_id, phase, message, data
        )

    def send_notification(event_name: str, status: str, duration: int | None) -> None:
        # US-2.16: once the branch head is resolved, include the SHA in the
        # source; events fired before resolution (started) stay branch-only.
        if ctx.get("source") == "zip":
            source = f"zip {ctx.get('zip_filename') or ''}".strip()
        else:
            sha = state.get("commit_sha")
            source = f"branch {deployment['branch']}" + (
                f" @ {sha[:7]}" if sha else ""
            )
        notify.notify_deployment_event(
            settings,
            org_id=org_id,
            deployment_id=deployment["id"],
            deployment_name=deployment["name"],
            project_name=ctx.get("project_name") or "",
            project_id=deployment["project_id"],
            run_id=run_id,
            event=event_name,
            status=status,
            source=source,
            triggered_by=ctx.get("triggered_by") or "",
            duration_seconds=duration,
        )

    started = time.monotonic()
    send_notification("started", "running", None)
    try:
        await asyncio.wait_for(
            _pipeline_inner(settings, ctx, state, logline, flush_log, event),
            timeout=timeout_minutes * 60,
        )
        elapsed = int(time.monotonic() - started)
        logline(f"[deployment succeeded in {elapsed}s]")
        await asyncio.to_thread(flush_log)
        await asyncio.to_thread(
            _update_run,
            settings,
            run_id,
            {"status": "succeeded", "finished_at": _now()},
        )
        await asyncio.to_thread(_set_current_run, settings, deployment["id"], run_id)
        await asyncio.to_thread(_settle_release_deploy, settings, run_id, "succeeded")
        await _launch_release_suites_if_any(settings, run_id)
        await event("done", f"Deployment succeeded in {elapsed}s")
        send_notification("succeeded", "succeeded", elapsed)
    except asyncio.CancelledError:
        # US-1.35: honest semantics — nothing already done is undone.
        logline(
            "[cancelled — files already transferred and script steps already"
            " executed are NOT undone]"
        )
        try:
            await asyncio.to_thread(flush_log)
            await asyncio.to_thread(
                _update_run,
                settings,
                run_id,
                {"status": "cancelled", "finished_at": _now()},
            )
            await asyncio.to_thread(
                _settle_release_deploy,
                settings,
                run_id,
                "cancelled",
                "the UAT deploy was cancelled",
            )
            await event("error", "Run cancelled")
            send_notification("cancelled", "cancelled", int(time.monotonic() - started))
        except Exception:
            pass
    except asyncio.TimeoutError:
        note = f"Run timed out after {timeout_minutes} minutes"
        logline(f"[{note}]")
        await asyncio.to_thread(flush_log)
        await asyncio.to_thread(
            _update_run, settings, run_id, {"status": "failed", "finished_at": _now()}
        )
        await asyncio.to_thread(
            _settle_release_deploy, settings, run_id, "failed", f"the UAT deploy {note[0].lower()}{note[1:]}"
        )
        await event("error", note)
        send_notification("failed", "failed", int(time.monotonic() - started))
    except Exception as e:  # noqa: BLE001 — a run must always reach a terminal state
        note = getattr(e, "message", str(e)) or e.__class__.__name__
        logline(f"[failed: {note}]")
        try:
            await asyncio.to_thread(flush_log)
            await asyncio.to_thread(
                _update_run,
                settings,
                run_id,
                {"status": "failed", "finished_at": _now()},
            )
            # US-120.1: the pipeline's own last word reaches the release page.
            await asyncio.to_thread(
                _settle_release_deploy, settings, run_id, "failed", f"the UAT deploy failed: {note}"
            )
            await event("error", note)
            send_notification("failed", "failed", int(time.monotonic() - started))
        except Exception:
            pass  # DB gone — the startup reaper will pick the run up


def _now() -> str:
    # Postgres accepts ISO strings; avoid importing datetime everywhere.
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).isoformat()


async def _pipeline_inner(
    settings: Settings,
    ctx: dict[str, Any],
    state: dict[str, Any],
    logline,
    flush_log,
    event,
) -> None:
    """US-50.5: `state` is run_pipeline's — passed in, not closed over.

    It carries the resolved commit back out so the notification can name it
    (US-2.16). Reaching for it as a free variable is what made every
    branch-source run fail with `name 'state' is not defined`.
    """
    run_id: str = ctx["run_id"]
    org_id: str = ctx["org_id"]
    deployment: dict = ctx["deployment"]
    server: dict = ctx["server"]
    repo_full_name: str = ctx["repo_full_name"]
    owner, repo = repo_full_name.split("/", 1)
    branch: str = deployment["branch"]
    target: str = deployment["target_folder"]
    strategy: str = deployment.get("strategy") or "in-place"
    keep_releases: int = deployment.get("keep_releases") or 5

    await asyncio.to_thread(
        _update_run, settings, run_id, {"status": "running", "started_at": _now()}
    )

    # --- preflight (US-1.38): fail in seconds, before any fetch ---------
    await event(
        "preflight", f"Connecting to {server['host']} as {server['username']}"
    )
    source: str = ctx.get("source") or "branch"
    archive_ctx = ctx.get("archive")  # US-1.47 redeploy / US-1.43 fallback
    payload_ext: str = (
        archive_ctx["ext"] if archive_ctx else ("zip" if source == "zip" else "tgz")
    )
    # US-2.14: payload size is known before fetch for staged zips and
    # archived/promoted artifacts; branch deploys fall back to the floor.
    if archive_ctx:
        payload_bytes = archive_ctx.get("bytes")
    elif source == "zip":
        payload_bytes = deployment.get("staged_zip_bytes")
    else:
        payload_bytes = None
    required_mb, space_reason = compute_required_free_mb(
        payload_bytes, deployment.get("strategy") or "in-place", keep_releases
    )
    conn = await connect_to_server(settings, server)
    try:
        checks = await asyncio.to_thread(
            preflight_checks,
            conn.transport,
            target,
            required_mb,
            ("unzip",) if payload_ext == "zip" else ("tar",),
            space_reason,
        )
        for c in checks:
            await event(
                "preflight",
                f"{'✓' if c['ok'] else '✗'} {c['check']}: {c['detail']}",
            )
            logline(f"[preflight {'ok' if c['ok'] else 'FAILED'}] {c['check']}: {c['detail']}")
        failed = [c for c in checks if not c["ok"]]
        if failed:
            raise PipelineError(f"Preflight failed — {failed[0]['check']}: {failed[0]['detail']}")

        # --- fetch ---------------------------------------------------------
        tmp = tempfile.NamedTemporaryFile(
            prefix="sf-deploy-", suffix=f".{payload_ext}", delete=False
        )
        tmp.close()
        try:
            if archive_ctx:
                # US-1.47: the archived bytes are the payload — no GitHub,
                # no staged zip. Works even if the branch was rewritten.
                await event(
                    "fetch", f"Fetching archived payload {archive_ctx['label']}"
                )
                data = await storage.get_object(settings, archive_ctx["path"])
                if data is None:
                    raise PipelineError("Archived payload is missing from storage.")
                with open(tmp.name, "wb") as f:
                    f.write(data)
                logline(f"[deploying archived payload {archive_ctx['label']}]")
                await event(
                    "fetch",
                    f"Archived payload ready ({len(data) / 1_048_576:.1f} MB)",
                    {"bytes": len(data)},
                )
            elif source == "zip":
                # US-1.33: the staged artifact is the payload — no GitHub.
                zip_name = ctx.get("zip_filename") or "staged.zip"
                await event("fetch", f"Fetching staged zip {zip_name}")
                data = await storage.get_object(
                    settings,
                    f"{storage.deployment_prefix(org_id, deployment['id'])}/staged.zip",
                )
                if data is None:
                    raise PipelineError("No staged zip found for this deployment.")
                with open(tmp.name, "wb") as f:
                    f.write(data)
                logline(
                    f"[deploying staged zip {zip_name}"
                    f" ({len(data) / 1_048_576:.1f} MB)]"
                )
                await event(
                    "fetch",
                    f"Staged zip ready ({len(data) / 1_048_576:.1f} MB)",
                    {"bytes": len(data)},
                )
            else:
                try:
                    token = await github_tokens.token_for_org(
                        settings, org_id, ctx.get("repo_full_name")
                    )
                except github.GitHubError as e:
                    raise PipelineError(str(e))
                override = ctx.get("override")
                if override:
                    # US-1.50: one-off ref, resolved by the endpoint already.
                    sha = override["sha"]
                    message = override.get("message") or ""
                    logline(
                        f"[deploying OVERRIDE ref {override['ref']} @ {sha[:7]}"
                        f" — configured branch {branch} unchanged]"
                    )
                    await event(
                        "fetch",
                        f"Override ref {override['ref']} @ {sha[:7]} — downloading archive",
                    )
                else:
                    await event(
                        "fetch", f"Resolving head of {branch} in {repo_full_name}"
                    )
                    try:
                        head = await github.get_branch(token, owner, repo, branch)
                    except github.GitHubError as e:
                        raise PipelineError(
                            f"Could not resolve branch '{branch}': {e.message}"
                        )
                    sha = head["commit"]["sha"]
                    message = (
                        (head["commit"].get("commit") or {}).get("message") or ""
                    ).strip()
                    logline(f"[deploying {repo_full_name}@{sha[:7]} (branch {branch})]")
                    await event("fetch", f"Head is {sha[:7]} — downloading archive")
                state["commit_sha"] = sha  # US-2.16: notifications include it
                await asyncio.to_thread(
                    _update_run,
                    settings,
                    run_id,
                    {
                        "commit_sha": sha,
                        "commit_message": message.splitlines()[0][:200] if message else None,
                    },
                )
                size = await github.download_tarball(token, owner, repo, sha, tmp.name)
                await event(
                    "fetch",
                    f"Downloaded {size / 1_048_576:.1f} MB archive",
                    {"bytes": size},
                )

                # US-1.36: source folder + excludes apply to branch payloads.
                src_folder = (deployment.get("source_folder") or "").strip().strip("/")
                patterns_raw = deployment.get("exclude_patterns") or ""
                if src_folder or patterns_raw.strip():
                    await event(
                        "fetch",
                        f"Filtering payload — source folder: {src_folder or 'repo root'}"
                        + (", exclude patterns applied" if patterns_raw.strip() else ""),
                    )
                    filtered = tmp.name + ".filtered"
                    kept = await asyncio.to_thread(
                        filter_tarball, tmp.name, filtered, src_folder, patterns_raw
                    )
                    if kept == 0:
                        raise PipelineError(
                            f"Source folder '{src_folder}' has no files at commit"
                            f" {sha[:7]} — check the path."
                        )
                    os.replace(filtered, tmp.name)
                    logline(
                        f"[payload filtered: source folder '{src_folder or '/'}',"
                        f" {kept} file(s) kept]"
                    )
                    for p in patterns_raw.splitlines():
                        if p.strip():
                            logline(f"[exclude: {p.strip()}]")
                    await event("fetch", f"Filtered payload: {kept} file(s)")

            # --- transfer ---------------------------------------------------
            remote_tmp = f"/tmp/sf-deploy-{run_id}.{payload_ext}"
            await event("transfer", f"Uploading archive to {server['host']}")
            last_tick = {"t": 0.0}

            def progress(done: int, total: int) -> None:
                now = time.monotonic()
                if now - last_tick["t"] < UPLOAD_EVENT_INTERVAL_SECONDS:
                    return
                last_tick["t"] = now
                record_event(
                    settings,
                    org_id,
                    run_id,
                    "transfer",
                    f"Uploading… {done / 1_048_576:.1f} / {total / 1_048_576:.1f} MB",
                    {"bytes_done": done, "bytes_total": total},
                )

            await asyncio.to_thread(
                _upload, conn.transport, tmp.name, remote_tmp, progress
            )
            await event("transfer", "Upload complete")

            # --- extract --------------------------------------------------
            # Releases strategy (US-1.39): the payload lands in its own
            # release folder; the live app is untouched until the flip.
            if strategy == "releases":
                stamp = time.strftime("%Y%m%d%H%M%S", time.gmtime())
                dest = f"{target}/releases/{stamp}-{run_id[:8]}"
                await asyncio.to_thread(
                    _update_run, settings, run_id, {"release_path": dest}
                )
            else:
                dest = target
            await event("extract", f"Extracting into {dest}")
            q_tmp = shlex.quote(remote_tmp)
            q_target = shlex.quote(target)
            q_dest = shlex.quote(dest)
            extract_lines: list[str] = []
            if payload_ext == "zip":
                # A zip is a prepared artifact — its contents land as-is
                # (no wrapper stripping, US-1.33/1.36).
                extract_cmd = (
                    "command -v unzip >/dev/null 2>&1"
                    " || { echo '__NO_TOOL__' >&2; exit 3; }; "
                    f"mkdir -p {q_dest} && "
                    f"unzip -o {q_tmp} -d {q_dest} >/dev/null && "
                    f"rm -f {q_tmp}"
                )
            else:
                extract_cmd = (
                    "command -v tar >/dev/null 2>&1"
                    " || { echo '__NO_TOOL__' >&2; exit 3; }; "
                    f"mkdir -p {q_dest} && "
                    f"tar -xzf {q_tmp} --strip-components=1 -C {q_dest} && "
                    f"rm -f {q_tmp}"
                )
            status = await asyncio.to_thread(
                _exec, conn.transport, extract_cmd, None, extract_lines.append
            )
            if status == 3 or any("__NO_TOOL__" in l for l in extract_lines):
                tool = "unzip" if payload_ext == "zip" else "tar"
                raise PipelineError(f"`{tool}` is not installed on this server.")
            if status != 0:
                detail = "\n".join(extract_lines[-5:])
                raise PipelineError(f"Extraction failed (exit {status}): {detail}")
            logline(f"[files extracted into {dest}]")
            await event("extract", "Files in place")

            # --- script ---------------------------------------------------
            script: str = deployment.get("script") or ""
            if script.strip():
                # US-1.37: values resolved server-side, injected as exports on
                # the script's stdin, masked out of every log line and event.
                env_values = await fetch_env_values(settings, org_id, deployment["id"])
                mask = make_masker(env_values)
                suffix = (
                    f" — {len(env_values)} env var(s) injected" if env_values else ""
                )
                await event("script", f"Running deployment script (sh -e){suffix}")

                line_count = {"n": 0}

                def on_line(line: str) -> None:
                    line = mask(line)
                    logline(line)
                    line_count["n"] += 1
                    record_event(settings, org_id, run_id, "script", line)
                    if line_count["n"] % LOG_FLUSH_EVERY_LINES == 0:
                        flush_log()

                exports = "".join(
                    f"export {name}={shlex.quote(value)}\n"
                    for name, value in env_values.items()
                )
                if strategy == "releases":
                    exports += (
                        f"export SF_RELEASE_PATH={q_dest}\n"
                        f"export SF_TARGET={q_target}\n"
                    )
                payload = exports + script.replace("\r\n", "\n")
                status = await asyncio.to_thread(
                    _exec,
                    conn.transport,
                    f"cd {q_dest} && exec /bin/sh -e -s",
                    payload.encode("utf-8"),
                    on_line,
                )
                await asyncio.to_thread(flush_log)
                if status != 0:
                    raise PipelineError(f"Deployment script exited with code {status}")
                await event("script", "Script finished (exit 0)")
            else:
                logline("[no deployment script — transfer-only deployment]")
                await event("script", "No script configured — skipped")

            # --- release flip + retention (US-1.39, releases only) --------
            prev_release: str | None = None
            if strategy == "releases":
                # Remember what was live — the US-1.40 auto-rollback target.
                prev_lines: list[str] = []
                await asyncio.to_thread(
                    _exec,
                    conn.transport,
                    f"readlink {q_target}/current 2>/dev/null || true",
                    None,
                    prev_lines.append,
                )
                prev_release = (
                    prev_lines[0].strip() if prev_lines and prev_lines[0].strip() else None
                )

                await event("release", "Activating release (atomic symlink flip)")
                flip_lines: list[str] = []
                flip_cmd = (
                    f"ln -sfn {q_dest} {q_target}/.sf-current-tmp && "
                    f"mv -T {q_target}/.sf-current-tmp {q_target}/current"
                )
                status = await asyncio.to_thread(
                    _exec, conn.transport, flip_cmd, None, flip_lines.append
                )
                if status != 0:
                    raise PipelineError(
                        "Could not activate the release (symlink flip failed): "
                        + ("; ".join(flip_lines[-3:]) or f"exit {status}")
                    )
                logline(f"[current -> {dest}]")
                await event("release", "Release is live — `current` repointed")

                prune_cmd = (
                    f'cur=$(basename "$(readlink {q_target}/current)"); '
                    f"cd {q_target}/releases && ls -1 | sort | "
                    f"head -n -{int(keep_releases)} | "
                    'while read -r d; do if [ "$d" != "$cur" ]; then '
                    'rm -rf -- "$d" && echo "pruned:$d"; fi; done'
                )
                prune_lines: list[str] = []
                await asyncio.to_thread(
                    _exec, conn.transport, prune_cmd, None, prune_lines.append
                )
                pruned_names = [
                    l.split(":", 1)[1] for l in prune_lines if l.startswith("pruned:")
                ]
                for name in pruned_names:
                    await asyncio.to_thread(
                        _null_pruned_release,
                        settings,
                        deployment["id"],
                        f"{target}/releases/{name}",
                    )
                    logline(f"[retention pruned release {name}]")
                if pruned_names:
                    await event(
                        "release",
                        f"Retention: pruned {len(pruned_names)} old release(s),"
                        f" keeping last {keep_releases}",
                    )

            # --- verify (US-1.40): the run only succeeds when the app answers
            hc_url = (deployment.get("health_check_url") or "").strip()
            if hc_url:
                expected = deployment.get("health_check_expected_status") or 200
                window = deployment.get("health_check_window_seconds") or 60
                delay = deployment.get("health_check_initial_delay_seconds") or 0
                await event(
                    "verify",
                    f"Health check: {hc_url} (expect {expected}, up to {window}s)",
                )
                if delay:
                    await asyncio.sleep(delay)
                deadline = time.monotonic() + window
                healthy, last = False, ""
                while True:
                    healthy, last = await asyncio.to_thread(
                        health_check_once, conn.transport, hc_url, expected
                    )
                    if healthy or time.monotonic() >= deadline:
                        break
                    await event("verify", f"Not healthy yet ({last}) — retrying")
                    await asyncio.sleep(3)
                if healthy:
                    logline(f"[health check passed: {hc_url} -> {last}]")
                    await event("verify", f"Health check passed ({last})")
                else:
                    logline(f"[health check FAILED — last response: {last}]")
                    if strategy == "releases" and prev_release:
                        await event(
                            "release",
                            "AUTO-ROLLBACK: health check failed — repointing"
                            " `current` to the previous release",
                        )
                        flip_back = (
                            f"ln -sfn {shlex.quote(prev_release)}"
                            f" {q_target}/.sf-current-tmp && "
                            f"mv -T {q_target}/.sf-current-tmp {q_target}/current"
                        )
                        back_status = await asyncio.to_thread(
                            _exec, conn.transport, flip_back
                        )
                        if back_status == 0:
                            await asyncio.to_thread(
                                record_auto_rollback,
                                settings,
                                deployment,
                                prev_release,
                                run_id,
                            )
                            logline(f"[AUTO-ROLLBACK: current -> {prev_release}]")
                            await event(
                                "release",
                                "Auto-rollback complete — previous release is live",
                            )
                            notify.notify_deployment_event(
                                settings,
                                org_id=org_id,
                                deployment_id=deployment["id"],
                                deployment_name=deployment["name"],
                                project_name=ctx.get("project_name") or "",
                                project_id=deployment["project_id"],
                                run_id=run_id,
                                event="rolled_back",
                                status="failed",
                                source="auto-rollback (failed health check)",
                                triggered_by="auto (failed health check)",
                                duration_seconds=None,
                            )
                        else:
                            logline(
                                "[AUTO-ROLLBACK FAILED — `current` may point"
                                " at the unhealthy release]"
                            )
                            await event("error", "Auto-rollback failed")
                    raise PipelineError(
                        f"Health check failed (last response: {last})"
                    )

            # --- archive (US-1.47): byte-exact copy of what shipped --------
            try:
                payload_bytes = os.path.getsize(tmp.name)
                if payload_bytes <= ARCHIVE_MAX_BYTES:
                    artifact_path = (
                        f"{storage.deployment_prefix(org_id, deployment['id'])}"
                        f"/runs/{run_id}.{payload_ext}"
                    )
                    with open(tmp.name, "rb") as f:
                        payload_data = f.read()
                    import hashlib

                    digest = hashlib.sha256(payload_data).hexdigest()
                    await storage.put_object(settings, artifact_path, payload_data)
                    await asyncio.to_thread(
                        _update_run,
                        settings,
                        run_id,
                        {
                            "artifact_path": artifact_path,
                            "artifact_bytes": payload_bytes,
                            "artifact_sha256": digest,
                        },
                    )
                    logline(f"[payload archived: {artifact_path}]")
                    await event(
                        "archive",
                        f"Archived exact payload ({payload_bytes / 1_048_576:.1f} MB,"
                        f" sha256 {digest[:12]}…)",
                    )
                else:
                    logline("[payload exceeds the archive size limit — not archived]")
                    await event(
                        "archive",
                        f"Payload exceeds the {ARCHIVE_MAX_BYTES // 1_048_576} MB"
                        " archive limit — this run cannot be redeployed from history",
                    )
            except Exception as e:  # noqa: BLE001 — never fail a healthy deploy
                logline(f"[archiving failed: {e}]")
                await event("archive", f"Archiving failed: {e}")
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# The merge pipeline (US-50.2) — external deployments
# ---------------------------------------------------------------------------
#
# An external deployment has no machine. Deploying it means one thing: the
# source lands on the branch the team's own CI watches. The run ends at the
# merge and says so — success means the merge commit exists on the target
# branch, not that anything was deployed, because the factory does not own
# that pipeline and will not pretend to observe it.


class MergeRefused(PipelineError):
    """GitHub refused the merge (conflict, required review, failing check).

    Carries the PR so the failure log can name the artifact that explains it —
    and so the run leaves it standing rather than tidying away the evidence.
    """

    def __init__(self, message: str, pr_url: str | None, pr_number: int | None):
        super().__init__(message)
        self.pr_url = pr_url
        self.pr_number = pr_number


async def _ensure_branch_at(
    token: str, owner: str, repo: str, name: str, sha: str
) -> None:
    """Create refs/heads/<name> at <sha> unless it already exists.

    A pull request's head must be a branch — GitHub will not open one from a
    bare commit. So a run pinned to a commit (a ref override, or a release cut
    before us-50.4 gave it a branch) materializes one deterministically; the
    result on the target branch is identical either way.
    """
    existing = await github.get_ref(token, owner, repo, name)
    if existing:
        return
    await github.create_ref(token, owner, repo, name, sha)


async def run_merge_pipeline(settings: Settings, ctx: dict[str, Any]) -> None:
    """Terminal-state wrapper around the merge, mirroring run_pipeline so the
    run panel, the notifications and the cancel path need no new shape."""
    run_id: str = ctx["run_id"]
    org_id: str = ctx["org_id"]
    deployment: dict = ctx["deployment"]
    timeout_minutes: int = deployment.get("run_timeout_minutes") or 30

    state: dict[str, Any] = {"log": [], "commit_sha": None, "merged": False}

    def logline(line: str) -> None:
        state["log"].append(line)

    def flush_log() -> None:
        _update_run(settings, run_id, {"log": "\n".join(state["log"])})

    async def event(phase: str, message: str, data: dict | None = None) -> None:
        await asyncio.to_thread(
            record_event, settings, org_id, run_id, phase, message, data
        )

    def send_notification(event_name: str, status: str, duration: int | None) -> None:
        sha = state.get("commit_sha")
        source = f"branch {deployment.get('branch')}" + (
            f" @ {sha[:7]}" if sha else ""
        )
        notify.notify_deployment_event(
            settings,
            org_id=org_id,
            deployment_id=deployment["id"],
            deployment_name=deployment["name"],
            project_name=ctx.get("project_name") or "",
            project_id=deployment["project_id"],
            run_id=run_id,
            event=event_name,
            status=status,
            source=source,
            triggered_by=ctx.get("triggered_by") or "",
            duration_seconds=duration,
        )

    started = time.monotonic()
    send_notification("started", "running", None)
    try:
        await asyncio.wait_for(
            _merge_inner(settings, ctx, state, logline, event),
            timeout=timeout_minutes * 60,
        )
        elapsed = int(time.monotonic() - started)
        logline(f"[merge run finished in {elapsed}s]")
        await asyncio.to_thread(flush_log)
        await asyncio.to_thread(
            _update_run,
            settings,
            run_id,
            {"status": "succeeded", "finished_at": _now()},
        )
        await asyncio.to_thread(_set_current_run, settings, deployment["id"], run_id)
        await asyncio.to_thread(_settle_release_deploy, settings, run_id, "succeeded")
        await _launch_release_suites_if_any(settings, run_id)
        # A no-op says so rather than claiming a merge — "already live" is the
        # ordinary outcome of re-deploying the current build.
        await event(
            "done",
            f"Merged in {elapsed}s"
            if state.get("merged")
            else f"Nothing to merge — {deployment.get('target_branch')}"
            " already had this commit",
        )
        send_notification("succeeded", "succeeded", elapsed)
    except asyncio.CancelledError:
        # Nothing already merged is undone; a cancel before the merge simply
        # leaves the target branch as it was.
        logline(
            "[cancelled — a merge that already landed is NOT undone; one that"
            " had not happened yet did not]"
        )
        try:
            await asyncio.to_thread(flush_log)
            await asyncio.to_thread(
                _update_run,
                settings,
                run_id,
                {"status": "cancelled", "finished_at": _now()},
            )
            await asyncio.to_thread(
                _settle_release_deploy,
                settings,
                run_id,
                "cancelled",
                "the UAT deploy was cancelled",
            )
            await event("error", "Run cancelled")
            send_notification("cancelled", "cancelled", int(time.monotonic() - started))
        except Exception:
            pass
    except asyncio.TimeoutError:
        note = f"Run timed out after {timeout_minutes} minutes"
        logline(f"[{note}]")
        await asyncio.to_thread(flush_log)
        await asyncio.to_thread(
            _update_run, settings, run_id, {"status": "failed", "finished_at": _now()}
        )
        await asyncio.to_thread(
            _settle_release_deploy, settings, run_id, "failed", f"the UAT deploy {note[0].lower()}{note[1:]}"
        )
        await event("error", note)
        send_notification("failed", "failed", int(time.monotonic() - started))
    except Exception as e:  # noqa: BLE001 — a run must always reach a terminal state
        note = getattr(e, "message", str(e)) or e.__class__.__name__
        pr_url = getattr(e, "pr_url", None)
        logline(f"[failed: {note}]")
        if pr_url:
            logline(f"[the pull request is left open: {pr_url}]")
        try:
            await asyncio.to_thread(flush_log)
            await asyncio.to_thread(
                _update_run,
                settings,
                run_id,
                {"status": "failed", "finished_at": _now()},
            )
            await asyncio.to_thread(
                _settle_release_deploy, settings, run_id, "failed", f"the UAT deploy failed: {note}"
            )
            await event("error", note)
            if pr_url:
                await event("error", f"Pull request left open: {pr_url}")
            send_notification("failed", "failed", int(time.monotonic() - started))
        except Exception:
            pass


async def _merge_inner(
    settings: Settings,
    ctx: dict[str, Any],
    state: dict[str, Any],
    logline,
    event,
) -> None:
    """resolve the source commit -> open or reuse the pull request -> merge.

    Each stage appends a `deployment_run_events` row the way transfer and
    script stages already do, so the existing run panel renders it unchanged.
    """
    run_id: str = ctx["run_id"]
    org_id: str = ctx["org_id"]
    deployment: dict = ctx["deployment"]
    repo_full_name: str = ctx.get("repo_full_name") or ""
    if "/" not in repo_full_name:
        raise PipelineError("This project has no connected GitHub repository.")
    owner, repo = repo_full_name.split("/", 1)
    branch: str = deployment["branch"]
    target: str = (deployment.get("target_branch") or "").strip()
    if not target:
        raise PipelineError("This deployment has no target branch to merge into.")

    await asyncio.to_thread(
        _update_run, settings, run_id, {"status": "running", "started_at": _now()}
    )

    try:
        token = await github_tokens.token_for_org(settings, org_id, repo_full_name)
    except github.GitHubError as e:
        raise PipelineError(str(e))

    # --- resolve the source commit --------------------------------------
    override = ctx.get("override")
    if override:
        sha = override["sha"]
        message = override.get("message") or ""
        # us-50.4: a release carries the branch it was cut as; anything else
        # pinned to a bare commit gets a deterministic branch to open from.
        head_ref = (override.get("branch") or "").strip() or (
            f"buildmill-deploy/{sha[:12]}"
        )
        await event(
            "fetch", f"Source is {override['ref']} @ {sha[:7]} (pinned)"
        )
        logline(
            f"[merging PINNED ref {override['ref']} @ {sha[:7]}"
            f" — configured source branch {branch} unchanged]"
        )
        await _ensure_branch_at(token, owner, repo, head_ref, sha)
    else:
        head_ref = branch
        await event("fetch", f"Resolving head of {branch} in {repo_full_name}")
        try:
            head = await github.get_branch(token, owner, repo, branch)
        except github.GitHubError as e:
            raise PipelineError(f"Could not resolve branch '{branch}': {e.message}")
        sha = head["commit"]["sha"]
        message = ((head["commit"].get("commit") or {}).get("message") or "").strip()
        logline(f"[merging {repo_full_name} {branch} @ {sha[:7]} into {target}]")
        await event("fetch", f"Head of {branch} is {sha[:7]}")

    state["commit_sha"] = sha
    await asyncio.to_thread(
        _update_run,
        settings,
        run_id,
        {
            "commit_sha": sha,
            "commit_message": message.splitlines()[0][:200] if message else None,
        },
    )

    # --- already there? --------------------------------------------------
    # Re-deploying the current build is the ordinary case, and a red run for
    # "already live" would train the manager to ignore red.
    try:
        cmp = await github.compare_commits(token, owner, repo, target, sha)
    except github.GitHubError as e:
        raise PipelineError(f"Could not compare {target} with {sha[:7]}: {e.message}")
    if cmp.get("status") in ("identical", "behind"):
        logline(f"[nothing to merge — {target} already contains {sha[:7]}]")
        await event(
            "release", f"Nothing to merge — {target} already contains {sha[:7]}"
        )
        return

    # --- open or reuse the pull request ----------------------------------
    await event("transfer", f"Preparing the pull request {head_ref} → {target}")
    try:
        pull = await github.find_open_pull(token, owner, repo, head_ref, target)
    except github.GitHubError as e:
        raise PipelineError(f"Could not look for an existing pull request: {e.message}")
    if pull:
        logline(f"[reusing open pull request #{pull['number']}: {pull['html_url']}]")
        await event(
            "transfer",
            f"Reusing open pull request #{pull['number']} — {pull['html_url']}",
        )
    else:
        title = f"Deploy {head_ref} → {target}"
        body = (
            f"Opened by Build Mill for the **{deployment.get('name')}** "
            f"deployment.\n\nMerging `{head_ref}` (`{sha[:7]}`) into "
            f"`{target}`. This branch move is the deployment — whatever "
            "pipeline watches `" + target + "` takes it from here."
        )
        try:
            pull = await github.create_pull(
                token, owner, repo, head_ref, target, title, body
            )
        except github.GitHubError as e:
            raise PipelineError(str(e))
        logline(f"[opened pull request #{pull['number']}: {pull['html_url']}]")
        await event(
            "transfer",
            f"Opened pull request #{pull['number']} — {pull['html_url']}",
        )
    await asyncio.to_thread(
        _update_run, settings, run_id, {"pr_number": int(pull["number"])}
    )

    # --- merge -----------------------------------------------------------
    await event("release", f"Merging #{pull['number']} into {target}")
    try:
        merge_sha = await github.merge_pull_request(
            token, owner, repo, int(pull["number"]), merge_method="merge"
        )
    except github.GitHubError as e:
        # The PR stands. Deleting it to make the run look clean would throw
        # away the only artifact that explains why it stopped.
        raise MergeRefused(
            e.message, pull.get("html_url"), int(pull["number"])
        )
    await asyncio.to_thread(
        _update_run, settings, run_id, {"merge_commit_sha": merge_sha}
    )
    state["merged"] = True
    logline(
        f"[merged #{pull['number']}: {head_ref} @ {sha[:7]}"
        f" -> {target} @ {(merge_sha or '')[:7]}]"
    )
    logline(
        "[the merge is the whole deployment — whatever pipeline watches"
        f" {target} takes it from here]"
    )
    await event(
        "release",
        f"Merged {sha[:7]} into {target} as {(merge_sha or '?')[:7]}"
        f" (pull request #{pull['number']})",
    )


def launch_rollback(settings: Settings, ctx: dict[str, Any]) -> None:
    task = asyncio.get_running_loop().create_task(run_rollback(settings, ctx))
    _register(ctx["run_id"], task)


async def run_rollback(settings: Settings, ctx: dict[str, Any]) -> None:
    """Rollback (US-1.39): repoint `current` to a retained release — no
    transfer, no script. ctx: run_id, org_id, deployment, server,
    to_run (the run whose release goes live), project_name, triggered_by."""
    run_id: str = ctx["run_id"]
    org_id: str = ctx["org_id"]
    deployment: dict = ctx["deployment"]
    server: dict = ctx["server"]
    to_run: dict = ctx["to_run"]
    release_path: str = to_run["release_path"]
    target: str = deployment["target_folder"]
    log: list[str] = []

    async def event(phase: str, message: str) -> None:
        await asyncio.to_thread(record_event, settings, org_id, run_id, phase, message)

    def finish(status: str) -> None:
        _update_run(
            settings,
            run_id,
            {"status": status, "log": "\n".join(log), "finished_at": _now()},
        )

    def send_notification(event_name: str, status: str) -> None:
        notify.notify_deployment_event(
            settings,
            org_id=org_id,
            deployment_id=deployment["id"],
            deployment_name=deployment["name"],
            project_name=ctx.get("project_name") or "",
            project_id=deployment["project_id"],
            run_id=run_id,
            event=event_name,
            status=status,
            source=f"rollback to {release_path.rsplit('/', 1)[-1]}",
            triggered_by=ctx.get("triggered_by") or "",
            duration_seconds=None,
        )

    try:
        await asyncio.to_thread(
            _update_run, settings, run_id, {"status": "running", "started_at": _now()}
        )
        await event("release", f"Rolling back to {release_path}")
        conn = await connect_to_server(settings, server)
        try:
            q_target = shlex.quote(target)
            q_release = shlex.quote(release_path)
            exists = await asyncio.to_thread(
                _exec, conn.transport, f"[ -d {q_release} ]"
            )
            if exists != 0:
                raise PipelineError(
                    f"Release folder {release_path} no longer exists on the server"
                    " (pruned?) — redeploy from the archived payload instead."
                )
            lines: list[str] = []
            flip_cmd = (
                f"ln -sfn {q_release} {q_target}/.sf-current-tmp && "
                f"mv -T {q_target}/.sf-current-tmp {q_target}/current"
            )
            status = await asyncio.to_thread(
                _exec, conn.transport, flip_cmd, None, lines.append
            )
            if status != 0:
                raise PipelineError(
                    "Symlink flip failed: " + ("; ".join(lines[-3:]) or f"exit {status}")
                )
        finally:
            conn.close()
        log.append(f"[rolled back: current -> {release_path}]")
        log.append(f"[restores run {to_run['id']} — no files transferred]")
        await asyncio.to_thread(finish, "succeeded")
        await asyncio.to_thread(_set_current_run, settings, deployment["id"], run_id)
        await event("done", "Rollback complete — `current` repointed")
        send_notification("rolled_back", "succeeded")
    except asyncio.CancelledError:
        log.append("[rollback cancelled]")
        try:
            await asyncio.to_thread(finish, "cancelled")
            await event("error", "Rollback cancelled")
        except Exception:
            pass
    except Exception as e:  # noqa: BLE001 — must reach a terminal state
        note = getattr(e, "message", str(e)) or e.__class__.__name__
        log.append(f"[rollback failed: {note}]")
        try:
            await asyncio.to_thread(finish, "failed")
            await event("error", note)
            send_notification("failed", "failed")
        except Exception:
            pass
