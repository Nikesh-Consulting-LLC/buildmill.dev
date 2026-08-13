"""Suite run pipeline (US-81.2/81.3/81.4): deterministic test execution.

The factory's own answer to "did the tests pass" — a sibling of deploy.py
that checks out a PINNED commit on a registered server, runs the suite's
declared command against a deployed instance's URL, and parses the JUnit XML
it leaves behind. No LLM anywhere in this path: authoring specs is judgment
work that goes through review; running them is plumbing.

Terminal semantics the sign-off gate depends on:
  succeeded  - JUnit parsed, zero failures/errors
  failed     - JUnit parsed, some failures (the report is truth; the script's
               exit code is informative only)
  error      - could not test: SSH, preflight, missing/unparseable report.
               Not the same thing as tests failing, and worded differently.
  timed-out  - the wall clock (suite.timeout_minutes) won
  cancelled  - the API restarted or the task was cancelled mid-run

All writes use direct Postgres (service role equivalent), the deploy.py
reasoning: the pipeline outlives the triggering request. Authorization
happened wherever the run was created — the manual endpoint under the
caller's JWT, the release trigger under the release's own existence.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import tempfile
import time
from typing import Any

import paramiko

from . import github, github_tokens, notify
from .build_config import fetch_build_config_values
from .config import Settings
from .deploy import (
    PipelineError,
    _connect,
    _exec,
    _now,
    _upload,
    connect_to_server,
    make_masker,
    preflight_checks,
)
from .junit import JUnitParseError, JUnitReport, parse_junit

logger = logging.getLogger("uvicorn.error")

# Strong references to in-flight tasks (the deploy.py pattern).
_TASKS: set[asyncio.Task] = set()

# US-81.2 (out of scope note): suites sharing a server run sequentially in
# v1 — a browser suite saturating a UAT box while another suite runs would
# make both lie. Keyed by server id, per-process like the task registry.
_SERVER_LOCKS: dict[str, asyncio.Lock] = {}

LOG_FLUSH_EVERY_LINES = 25
RESULTS_MAX_BYTES = 10 * 1024 * 1024
WORKDIR_BASE = "/var/tmp/sf-suites"


class SuiteRunActive(Exception):
    """Single-flight: this suite already has a queued/running run."""


# ---------------------------------------------------------------------------
# Run records (sync, called via to_thread)
# ---------------------------------------------------------------------------


def create_suite_run(
    settings: Settings,
    *,
    org_id: str,
    project_id: str,
    suite_id: str,
    deployment_id: str,
    trigger: str,
    commit_sha: str,
    base_url: str,
    release_id: str | None = None,
) -> str:
    with _connect(settings) as conn:
        try:
            row = conn.execute(
                """
                insert into public.suite_runs
                  (org_id, project_id, suite_id, deployment_id, release_id,
                   trigger, commit_sha, base_url)
                values (%s, %s, %s, %s, %s, %s, %s, %s)
                returning id
                """,
                (
                    org_id,
                    project_id,
                    suite_id,
                    deployment_id,
                    release_id,
                    trigger,
                    commit_sha,
                    base_url,
                ),
            ).fetchone()
            conn.commit()
        except Exception as e:
            if "suite_runs_single_flight" in str(e):
                raise SuiteRunActive()
            raise
        return str(row["id"])


def record_event(
    settings: Settings,
    org_id: str,
    run_id: str,
    phase: str,
    message: str,
    data: dict[str, Any] | None = None,
) -> None:
    import json

    with _connect(settings) as conn:
        conn.execute(
            """
            insert into public.suite_run_events (org_id, run_id, phase, message, data)
            values (%s, %s, %s, %s, %s)
            """,
            (org_id, run_id, phase, message, json.dumps(data or {})),
        )
        conn.commit()


def _update_run(settings: Settings, run_id: str, fields: dict[str, Any]) -> None:
    sets = ", ".join(f"{k} = %s" for k in fields)
    with _connect(settings) as conn:
        conn.execute(
            f"update public.suite_runs set {sets} where id = %s",  # noqa: S608
            (*fields.values(), run_id),
        )
        conn.commit()


def reap_orphaned_runs(settings: Settings) -> int:
    """Fail suite runs stranded by an api restart — the deploy.py rule:
    nothing can legitimately be queued/running before this process existed."""
    with _connect(settings) as conn:
        rows = conn.execute(
            """
            update public.suite_runs
            set status = 'cancelled', finished_at = now(),
                error = 'interrupted by API server restart'
            where status in ('queued', 'running')
            returning id, org_id
            """
        ).fetchall()
        for row in rows:
            conn.execute(
                """
                insert into public.suite_run_events (org_id, run_id, phase, message)
                values (%s, %s, 'error', 'Run interrupted by API server restart')
                """,
                (row["org_id"], row["id"]),
            )
        conn.commit()
        return len(rows)


def _insert_tests(
    settings: Settings, org_id: str, run_id: str, report: JUnitReport
) -> None:
    with _connect(settings) as conn:
        for t in report.tests:
            conn.execute(
                """
                insert into public.suite_run_tests
                  (org_id, suite_run_id, spec_ref, status, duration_ms, message)
                values (%s, %s, %s, %s, %s, %s)
                """,
                (org_id, run_id, t.spec_ref, t.status, t.duration_ms, t.message),
            )
        conn.commit()


def map_results_to_release_cases(
    settings: Settings, *, run_id: str, suite_id: str, release_id: str
) -> int:
    """US-81.4: match this run's tests to the release's copied cases by
    (suite_id, spec_ref), then machine-upsert release_test_results for the
    matches. pass -> pass; fail/error -> fail; skipped gets no verdict — a
    test that did not run answered nothing, and the case blocks through the
    existing every-case-has-a-result rule until a human decides.

    Returns how many cases received a machine verdict. noted_by stays null
    (direct Postgres, no JWT) — that plus suite_run_id is what marks a row
    as a machine's in the UI."""
    with _connect(settings) as conn:
        conn.execute(
            """
            update public.suite_run_tests srt
            set test_case_id = tc.id
            from public.test_cases tc
            where srt.suite_run_id = %s
              and tc.release_id = %s
              and tc.suite_id = %s
              and tc.status = 'active'
              and tc.spec_ref = srt.spec_ref
            """,
            (run_id, release_id, suite_id),
        )
        rows = conn.execute(
            """
            insert into public.release_test_results
              (org_id, release_id, test_case_id, result, suite_run_id, noted_by)
            select srt.org_id, %s, srt.test_case_id,
                   case when srt.status = 'pass' then 'pass' else 'fail' end,
                   srt.suite_run_id, null
            from public.suite_run_tests srt
            where srt.suite_run_id = %s
              and srt.test_case_id is not null
              and srt.status <> 'skipped'
            on conflict (release_id, test_case_id) do update
              set result = excluded.result,
                  suite_run_id = excluded.suite_run_id,
                  comment = null,
                  noted_by = null,
                  noted_at = now()
            returning test_case_id
            """,
            (release_id, run_id),
        ).fetchall()
        conn.commit()
        return len(rows)


def waive_run(
    settings: Settings, *, run_id: str, waived_by: str, reason: str
) -> None:
    _update_run(
        settings,
        run_id,
        {"waived_at": _now(), "waived_by": waived_by, "waive_reason": reason},
    )


# ---------------------------------------------------------------------------
# Launch helpers
# ---------------------------------------------------------------------------


def _register(task: asyncio.Task) -> None:
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


def launch(settings: Settings, ctx: dict[str, Any]) -> None:
    """Fire-and-forget the pipeline on the running loop."""
    task = asyncio.get_running_loop().create_task(run_pipeline(settings, ctx))
    _register(task)


def _load_release_context(
    settings: Settings, release_id: str, environment: str
) -> list[dict[str, Any]]:
    """Everything the release trigger needs, one query set: the release, its
    project's designated deployment for `environment`, that deployment's
    server, and the project's active suites for the trigger."""
    flag = "run_on_uat" if environment == "uat" else "run_on_prod"
    with _connect(settings) as conn:
        rel = conn.execute(
            "select * from public.releases where id = %s", (release_id,)
        ).fetchone()
        if rel is None:
            return []
        project = conn.execute(
            "select * from public.projects where id = %s", (rel["project_id"],)
        ).fetchone()
        dep_col = (
            "release_uat_deployment_id"
            if environment == "uat"
            else "release_prod_deployment_id"
        )
        dep_id = project and project.get(dep_col)
        if not dep_id:
            return []
        deployment = conn.execute(
            "select * from public.deployments where id = %s", (dep_id,)
        ).fetchone()
        if deployment is None:
            return []
        suites = conn.execute(
            f"""
            select * from public.test_suites
            where project_id = %s and status = 'active' and {flag}
            order by name
            """,  # noqa: S608 - flag is one of two literals above
            (rel["project_id"],),
        ).fetchall()
        servers: dict[str, dict[str, Any]] = {}
        for suite in suites:
            sid = str(suite.get("server_id") or deployment["server_id"])
            if sid not in servers:
                servers[sid] = conn.execute(
                    "select * from public.servers where id = %s", (sid,)
                ).fetchone()
        out = []
        for suite in suites:
            sid = str(suite.get("server_id") or deployment["server_id"])
            out.append(
                {
                    "release": rel,
                    "project": project,
                    "deployment": deployment,
                    "suite": suite,
                    "server": servers.get(sid),
                }
            )
        return out


async def launch_release_suites(
    settings: Settings, release_id: str, *, environment: str = "uat"
) -> int:
    """US-81.3 (uat) / US-82.1 (production): fire every matching suite for a
    release, pinned to its commit, against its designated deployment. Returns
    how many were launched; a suite already in flight is skipped (the
    single-flight index is the referee, and the release page's rerun exists
    for the manager). Never raises — this runs off the deploy pipeline's
    settle path, which must not care."""
    trigger = "uat-deploy" if environment == "uat" else "prod-promote"
    try:
        rows = await asyncio.to_thread(
            _load_release_context, settings, release_id, environment
        )
    except Exception:
        logger.exception("suite launch: could not load release %s", release_id)
        return 0
    launched = 0
    for row in rows:
        suite = row["suite"]
        deployment = row["deployment"]
        release = row["release"]
        # A release that is not where this environment's testing happens gets
        # no runs — a stray late deploy callback must not start suites on a
        # cancelled or superseded release.
        wanted_status = "uat-deployed" if environment == "uat" else "released"
        if release["status"] != wanted_status:
            logger.info(
                "suite %s skipped: release %s is %s, not %s",
                suite["name"],
                release["version"],
                release["status"],
                wanted_status,
            )
            continue
        base_url = (deployment.get("website_url") or "").strip()
        if not base_url:
            logger.warning(
                "suite %s skipped: deployment %s has no website_url",
                suite["name"],
                deployment["id"],
            )
            continue
        try:
            run_id = await asyncio.to_thread(
                create_suite_run,
                settings,
                org_id=str(release["org_id"]),
                project_id=str(release["project_id"]),
                suite_id=str(suite["id"]),
                deployment_id=str(deployment["id"]),
                trigger=trigger,
                commit_sha=str(release["commit_sha"]),
                base_url=base_url,
                release_id=str(release["id"]),
            )
        except SuiteRunActive:
            continue
        except Exception:
            logger.exception("suite %s: could not create run", suite["name"])
            continue
        launch(
            settings,
            {
                "run_id": run_id,
                "org_id": str(release["org_id"]),
                "suite": suite,
                "deployment": deployment,
                "server": row["server"],
                "repo_full_name": (row["project"] or {}).get("repo_full_name") or "",
                "project": row["project"],
                "release": release,
                "commit_sha": str(release["commit_sha"]),
                "base_url": base_url,
            },
        )
        launched += 1
    return launched


# ---------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------


def _read_remote(
    transport: paramiko.Transport, path: str, max_bytes: int = RESULTS_MAX_BYTES
) -> bytes | None:
    sftp = paramiko.SFTPClient.from_transport(transport)
    if sftp is None:
        raise PipelineError("Could not open an SFTP channel.")
    try:
        with sftp.open(path, "rb") as f:
            return f.read(max_bytes)
    except FileNotFoundError:
        return None
    finally:
        sftp.close()


async def run_pipeline(settings: Settings, ctx: dict[str, Any]) -> None:
    run_id: str = ctx["run_id"]
    org_id: str = ctx["org_id"]
    suite: dict[str, Any] = ctx["suite"]
    deployment: dict[str, Any] = ctx["deployment"]
    server: dict[str, Any] | None = ctx.get("server")
    release: dict[str, Any] | None = ctx.get("release")
    commit_sha: str = ctx["commit_sha"]
    base_url: str = ctx["base_url"]

    log_lines: list[str] = []
    flushed = {"n": 0}

    def logline(line: str) -> None:
        log_lines.append(line)

    def flush_log() -> None:
        if len(log_lines) == flushed["n"]:
            return
        flushed["n"] = len(log_lines)
        _update_run(settings, run_id, {"log": "\n".join(log_lines)})

    async def event(phase: str, message: str, data: dict[str, Any] | None = None):
        await asyncio.to_thread(record_event, settings, org_id, run_id, phase, message, data)

    async def settle(status: str, error: str | None = None, **fields: Any) -> None:
        await asyncio.to_thread(flush_log)
        await asyncio.to_thread(
            _update_run,
            settings,
            run_id,
            {"status": status, "error": error, "finished_at": _now(), **fields},
        )
        await event(
            "done" if status == "succeeded" else "error",
            f"Suite {suite['name']}: {status}" + (f" — {error}" if error else ""),
        )
        if status != "succeeded":
            _notify_failure(settings, ctx, status, error)

    server_key = str((server or {}).get("id") or "none")
    lock = _SERVER_LOCKS.setdefault(server_key, asyncio.Lock())

    conn = None
    workdir = f"{WORKDIR_BASE}/{str(suite['id'])[:8]}"
    rundir = f"{workdir}/runs/{run_id[:8]}"
    cachedir = f"{workdir}/cache"
    remote_tmp = f"/tmp/sf-suite-{run_id[:8]}.tgz"
    timeout_seconds = int(suite.get("timeout_minutes") or 30) * 60

    try:
        async with lock:
            await asyncio.to_thread(
                _update_run, settings, run_id, {"status": "running", "started_at": _now()}
            )

            if server is None:
                raise PipelineError("No server to run this suite on.")

            # --- preflight ------------------------------------------------
            await event("preflight", f"Connecting to {server['host']}")
            conn = await connect_to_server(settings, server)
            checks = await asyncio.to_thread(
                preflight_checks,
                conn.transport,
                rundir,
                200,
                ("tar", "curl", "timeout"),
            )
            bad = [c for c in checks if not c["ok"]]
            for c in checks:
                await event(
                    "preflight", f"{'ok' if c['ok'] else 'FAIL'}: {c['detail']}"
                )
            if bad:
                raise PipelineError(
                    "Preflight failed: " + "; ".join(c["detail"] for c in bad)
                )

            # --- fetch ----------------------------------------------------
            repo_full_name = ctx.get("repo_full_name") or ""
            if "/" not in repo_full_name:
                raise PipelineError("Project has no GitHub repository configured.")
            owner, repo = repo_full_name.split("/", 1)
            try:
                token = await github_tokens.token_for_org(
                    settings, org_id, repo_full_name
                )
            except github.GitHubError as e:
                raise PipelineError(str(e))
            await event("fetch", f"Downloading {repo_full_name}@{commit_sha[:7]}")
            with tempfile.NamedTemporaryFile(suffix=".tgz", delete=False) as tmp:
                local_tmp = tmp.name
            try:
                size = await github.download_tarball(
                    token, owner, repo, commit_sha, local_tmp
                )
                await event(
                    "fetch",
                    f"Downloaded {size / 1_048_576:.1f} MB archive",
                    {"bytes": size},
                )

                # --- transfer + extract ---------------------------------
                await event("transfer", f"Uploading archive to {server['host']}")
                await asyncio.to_thread(
                    _upload, conn.transport, local_tmp, remote_tmp, lambda d, t: None
                )
            finally:
                try:
                    os.unlink(local_tmp)
                except OSError:
                    pass
            q_rundir = shlex.quote(rundir)
            q_cache = shlex.quote(cachedir)
            q_tmp = shlex.quote(remote_tmp)
            await event("extract", f"Extracting into {rundir}")
            extract_lines: list[str] = []
            status = await asyncio.to_thread(
                _exec,
                conn.transport,
                f"mkdir -p {q_rundir} {q_cache} && "
                f"tar -xzf {q_tmp} --strip-components=1 -C {q_rundir} && rm -f {q_tmp}",
                None,
                extract_lines.append,
            )
            if status != 0:
                detail = "\n".join(extract_lines[-5:])
                raise PipelineError(f"Extraction failed (exit {status}): {detail}")

            # --- script ---------------------------------------------------
            run_command: str = suite.get("run_command") or ""
            if not run_command.strip():
                raise PipelineError("This suite has no run command.")
            env_values = await fetch_build_config_values(
                settings, org_id, str(suite["project_id"])
            )
            mask = make_masker(env_values)
            results_path = (suite.get("results_path") or "test-results/junit.xml").strip()
            exports = "".join(
                f"export {name}={shlex.quote(value)}\n"
                for name, value in env_values.items()
            )
            exports += (
                f"export SF_BASE_URL={shlex.quote(base_url)}\n"
                f"export SF_COMMIT_SHA={shlex.quote(commit_sha)}\n"
                f"export SF_RELEASE_VERSION={shlex.quote(str((release or {}).get('version') or ''))}\n"
                f"export SF_RESULTS_PATH={shlex.quote(results_path)}\n"
                f"export SF_CACHE_DIR={q_cache}\n"
            )
            suffix = f" — {len(env_values)} build config value(s) injected" if env_values else ""
            await event(
                "script",
                f"Running suite command (sh -e, {timeout_seconds // 60} min limit){suffix}",
            )

            line_count = {"n": 0}

            def on_line(line: str) -> None:
                line = mask(line)
                logline(line)
                line_count["n"] += 1
                record_event(settings, org_id, run_id, "script", line)
                if line_count["n"] % LOG_FLUSH_EVERY_LINES == 0:
                    flush_log()

            payload = exports + run_command.replace("\r\n", "\n")
            try:
                exit_code = await asyncio.wait_for(
                    asyncio.to_thread(
                        _exec,
                        conn.transport,
                        f"cd {q_rundir} && exec timeout {timeout_seconds} /bin/sh -e -s",
                        payload.encode("utf-8"),
                        on_line,
                    ),
                    timeout=timeout_seconds + 120,
                )
            except asyncio.TimeoutError:
                await settle("timed-out", f"no exit after {timeout_seconds}s (transport hang)")
                return
            await asyncio.to_thread(flush_log)
            timed_out = exit_code == 124
            if timed_out:
                logline(f"[wall clock: suite exceeded {timeout_seconds // 60} minutes]")
            else:
                await event("script", f"Command finished (exit {exit_code})")

            # --- collect --------------------------------------------------
            await event("collect", f"Reading {results_path}")
            raw = await asyncio.to_thread(
                _read_remote, conn.transport, f"{rundir}/{results_path}"
            )
            report: JUnitReport | None = None
            parse_error: str | None = None
            if raw is None:
                parse_error = f"no report at {results_path}"
            else:
                try:
                    report = parse_junit(raw.decode("utf-8", "replace"))
                except JUnitParseError as e:
                    parse_error = str(e)

            if report is not None:
                await asyncio.to_thread(_insert_tests, settings, org_id, run_id, report)
                await event(
                    "collect",
                    f"{report.total} tests: {report.passed} passed, "
                    f"{report.failed} failed, {report.skipped} skipped",
                )

            # --- map (release runs only) ---------------------------------
            if report is not None and release is not None:
                mapped = await asyncio.to_thread(
                    map_results_to_release_cases,
                    settings,
                    run_id=run_id,
                    suite_id=str(suite["id"]),
                    release_id=str(release["id"]),
                )
                await event("map", f"{mapped} release case(s) answered")

            # --- cleanup --------------------------------------------------
            await asyncio.to_thread(
                _exec, conn.transport, f"rm -rf {q_rundir}", None, None
            )

            totals = (
                {
                    "tests_total": report.total,
                    "tests_passed": report.passed,
                    "tests_failed": report.failed,
                    "tests_skipped": report.skipped,
                }
                if report is not None
                else {}
            )
            if timed_out:
                await settle(
                    "timed-out",
                    f"exceeded {timeout_seconds // 60} minutes",
                    **totals,
                )
            elif report is None:
                # JUnit is truth, and there is none: could not test. The
                # exit code is the best clue we have.
                await settle(
                    "error",
                    f"{parse_error} (command exit {exit_code})",
                    **totals,
                )
            elif report.failed > 0:
                await settle(
                    "failed", f"{report.failed} of {report.total} tests failed", **totals
                )
            else:
                await settle("succeeded", None, **totals)

    except asyncio.CancelledError:
        try:
            await settle("cancelled", "cancelled")
        finally:
            raise
    except PipelineError as e:
        await settle("error", e.message)
    except Exception as e:  # noqa: BLE001 - terminal record beats a traceback
        logger.exception("suite run %s crashed", run_id)
        await settle("error", f"unexpected: {e}")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def _notify_failure(
    settings: Settings, ctx: dict[str, Any], status: str, error: str | None
) -> None:
    """A failed/errored suite is worth a ping. Rides the deployment
    notification channel with its own event name — deliveries go only to
    deployments that opted into 'suite-failed'."""
    try:
        deployment = ctx["deployment"]
        project = ctx.get("project") or {}
        notify.notify_deployment_event(
            settings,
            org_id=ctx["org_id"],
            deployment_id=str(deployment["id"]),
            deployment_name=str(deployment.get("name") or ""),
            project_name=str(project.get("name") or ""),
            project_id=str(deployment.get("project_id") or ""),
            run_id=ctx["run_id"],
            event="suite-failed",
            status=status,
            source=f"suite {ctx['suite']['name']}" + (f": {error}" if error else ""),
            triggered_by="suite-pipeline",
            duration_seconds=None,
        )
    except Exception:  # noqa: BLE001 - notification must never sink the run
        logger.exception("suite failure notification failed")
