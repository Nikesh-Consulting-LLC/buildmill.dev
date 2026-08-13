"""End-to-end exercise of the factory loop (US-1.9 → US-1.13).

Runs against the live dev stack: FastAPI on :8000, live Supabase, and the
runner with the simulated provider. Credentials come from apps/api/.env
(E2E_EMAIL / E2E_PASSWORD).

Scenarios:
  1. ok      — dispatch → runner succeeds → in-review → approve → merged
  2. stuck   — dispatch → provider hangs → timeout → failed
  3. fail    — dispatch → provider crashes → failed
  4. retry   — dispatch → in-review → reject w/ comment → re-dispatch →
               retry carries feedback + same branch → approve → merged

Usage: python tools/e2e_factory_loop.py
"""

import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
API = "http://localhost:8000"
RUNNER = ROOT / "apps" / "runner" / "runner.py"
PYTHON = ROOT / "apps" / "api" / ".venv" / "Scripts" / "python.exe"

RESULTS: list[tuple[str, bool, str]] = []


def env_value(key: str) -> str:
    for line in (ROOT / "apps" / "api" / ".env").read_text().splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(f"{key} not in apps/api/.env")


SUPABASE_URL = env_value("SUPABASE_URL")
ANON_KEY = env_value("SUPABASE_PUBLISHABLE_KEY")


def sign_in() -> str:
    resp = httpx.post(
        f"{SUPABASE_URL}/auth/v1/token",
        params={"grant_type": "password"},
        json={"email": env_value("E2E_EMAIL"), "password": env_value("E2E_PASSWORD")},
        headers={"apikey": ANON_KEY},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


class Client:
    def __init__(self, token: str):
        self.token = token
        self.pg = {"apikey": ANON_KEY, "Authorization": f"Bearer {token}"}

    def rest(self, method: str, path: str, **kw):
        resp = httpx.request(
            method, f"{SUPABASE_URL}/rest/v1/{path}", headers={**self.pg, **kw.pop("headers", {})}, timeout=15, **kw
        )
        resp.raise_for_status()
        return resp.json() if resp.text else None

    def api(self, method: str, path: str, expect_error: bool = False, **kw):
        resp = httpx.request(
            method,
            f"{API}{path}",
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=30,
            **kw,
        )
        if not expect_error:
            resp.raise_for_status()
        return resp

    # -- helpers -----------------------------------------------------------

    def org_id(self) -> str:
        return self.api("GET", "/api/v1/auth/me").json()["org_id"]

    def project_id(self) -> str:
        rows = self.rest("GET", "projects?select=id&limit=1")
        if not rows:
            raise SystemExit("no project — create one first")
        return rows[0]["id"]

    def create_task(self, org: str, project: str, title: str, story: str) -> str:
        rows = self.rest(
            "POST",
            "tasks",
            json={
                "org_id": org,
                "project_id": project,
                "title": title,
                "story": story,
                "acceptance_criteria": ["works end to end", "statuses recorded"],
            },
            headers={"Prefer": "return=representation"},
        )
        task_id = rows[0]["id"]
        # mirror the web UI, which logs a 'created' event alongside the insert
        self.rest(
            "POST",
            "task_events",
            json={
                "org_id": org,
                "task_id": task_id,
                "type": "created",
                "payload": {"title": title},
            },
        )
        return task_id

    def task(self, task_id: str) -> dict:
        return self.rest("GET", f"tasks?id=eq.{task_id}&select=*")[0]

    def latest_run(self, task_id: str) -> dict:
        rows = self.rest(
            "GET",
            f"runs?task_id=eq.{task_id}&select=*&order=created_at.desc&limit=1",
        )
        return rows[0]

    def events(self, task_id: str) -> list[str]:
        rows = self.rest(
            "GET", f"task_events?task_id=eq.{task_id}&select=type&order=created_at"
        )
        return [r["type"] for r in rows]


def run_runner(timeout_env: str = "120") -> str:
    import os

    proc = subprocess.run(
        [str(PYTHON), str(RUNNER), "--once"],
        capture_output=True,
        text=True,
        timeout=180,
        env={
            **os.environ,
            "FACTORY_API_URL": API,
            "RUNNER_TIMEOUT_SECONDS": timeout_env,
        },
        cwd=RUNNER.parent,
    )
    return proc.stdout + proc.stderr


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))


def scenario_ok(c: Client, org: str, project: str) -> None:
    print("\n[scenario 1: ok → approve → merged]")
    tid = c.create_task(org, project, "E2E: happy path task", "Just works. [sim:ok]")
    c.api("POST", f"/api/v1/tasks/{tid}/dispatch")
    check("dispatch sets queued", c.task(tid)["status"] == "queued")

    print(run_runner().strip())
    t = c.task(tid)
    check("runner success → in-review", t["status"] == "in-review", t["status"])
    run = c.latest_run(tid)
    check("run has diff + branch + pr", bool(run["diff"] and run["branch_ref"] and run["pr_url"]))

    # double-dispatch guard while not dispatchable
    resp = c.api("POST", f"/api/v1/tasks/{tid}/dispatch", expect_error=True)
    check("re-dispatch while in-review → 409", resp.status_code == 409)

    c.api("POST", f"/api/v1/runs/{run['id']}/approve")
    t = c.task(tid)
    check("approve → merged", t["status"] == "merged", t["status"])
    ev = c.events(tid)
    check(
        "event trail complete",
        ["created", "dispatched", "run-started", "run-succeeded", "approved", "merged"]
        == [e for e in ev if e != "updated"],
        ",".join(ev),
    )


def scenario_stuck(c: Client, org: str, project: str) -> None:
    print("\n[scenario 2: stuck → timeout → failed]")
    tid = c.create_task(org, project, "E2E: stuck provider [sim:stuck]", "Hangs.")
    c.api("POST", f"/api/v1/tasks/{tid}/dispatch")
    print(run_runner(timeout_env="5").strip())
    t = c.task(tid)
    run = c.latest_run(tid)
    check("stuck → failed", t["status"] == "failed", t["status"])
    check("timeout recorded in error", "stuck" in (run["error"] or ""), str(run["error"]))


def scenario_fail(c: Client, org: str, project: str) -> None:
    print("\n[scenario 3: provider crash → failed]")
    tid = c.create_task(org, project, "E2E: crashing provider [sim:fail]", "Crashes.")
    c.api("POST", f"/api/v1/tasks/{tid}/dispatch")
    print(run_runner().strip())
    t = c.task(tid)
    run = c.latest_run(tid)
    check("crash → failed", t["status"] == "failed", t["status"])
    check("error captured", "simulated failure" in (run["error"] or ""), str(run["error"]))


def scenario_retry(c: Client, org: str, project: str) -> None:
    print("\n[scenario 4: reject → informed retry → approve]")
    tid = c.create_task(org, project, "E2E: reject and retry task", "Needs a fix pass.")
    c.api("POST", f"/api/v1/tasks/{tid}/dispatch")
    print(run_runner().strip())
    first = c.latest_run(tid)

    # reject requires a comment
    resp = c.api(
        "POST", f"/api/v1/runs/{first['id']}/reject", json={"comment": "  "}, expect_error=True
    )
    check("empty rejection comment → 422", resp.status_code == 422)

    c.api(
        "POST",
        f"/api/v1/runs/{first['id']}/reject",
        json={"comment": "Please rename the endpoint to /healthz"},
    )
    check("reject → needs-fixes", c.task(tid)["status"] == "needs-fixes")

    c.api("POST", f"/api/v1/tasks/{tid}/dispatch")
    retry = c.latest_run(tid)
    ctx = retry["input_context"]
    check(
        "retry context carries feedback",
        ctx.get("feedback") == "Please rename the endpoint to /healthz",
        str(ctx.get("feedback")),
    )
    check(
        "retry context carries previous branch",
        ctx.get("previous_branch") == first["branch_ref"],
        f"{ctx.get('previous_branch')} vs {first['branch_ref']}",
    )

    print(run_runner().strip())
    retry = c.latest_run(tid)
    check(
        "retry continued on same branch",
        retry["branch_ref"] == first["branch_ref"],
        str(retry["branch_ref"]),
    )
    check(
        "retry diff addresses feedback",
        "addressed feedback" in (retry["diff"] or ""),
    )

    c.api("POST", f"/api/v1/runs/{retry['id']}/approve")
    check("approve retry → merged", c.task(tid)["status"] == "merged")
    ev = c.events(tid)
    check("rejected event recorded", "rejected" in ev, ",".join(ev))


def main() -> None:
    token = sign_in()
    c = Client(token)
    org = c.org_id()
    project = c.project_id()
    print(f"signed in; org {org[:8]}…, project {project[:8]}…")

    scenario_ok(c, org, project)
    scenario_stuck(c, org, project)
    scenario_fail(c, org, project)
    scenario_retry(c, org, project)

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{'=' * 60}\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    if failed:
        for name, _, detail in failed:
            print(f"  FAILED: {name} — {detail}")
        sys.exit(1)


if __name__ == "__main__":
    main()
