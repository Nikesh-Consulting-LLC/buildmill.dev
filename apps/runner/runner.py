"""Software Factory runner — worker #1 (US-3.5, formerly US-1.10).

DEPRECATED (US-10.10): the headless polling runner is superseded by the
server-controlled **supervisor runner** — run `python -m supervisor` instead
(see apps/runner/README.md). This script stays only for the local e2e harness
(tools/e2e_factory_loop.py) and will be removed once that migrates. New
deployments should not use RUNNER_PROVIDER; modules and model are configured
server-side.


The runner is just a registered `autonomous` worker: it polls the same
pool every worker claims from, pulls the context bundle, executes the
provider, and submits through the same contract. Heartbeats keep the
claim's lease alive while a long CLI execution runs; failures always
submit an error — a run never silently dies.

Providers are pluggable behind execute(input_context) -> ProviderResult.
Phase 1 ships the SIMULATED provider (see provider_sim.py); the real
Claude Code provider replaces it in US-1.15. Git work goes through the
factory git remote (context.git_remote_url) with this same worker
token — no GitHub credentials live on the runner.

Usage:
    python runner.py            # poll forever
    python runner.py --once     # process at most one run, then exit

Env:
    FACTORY_API_URL       default http://localhost:8000
    FACTORY_WORKER_TOKEN  minted on Settings → Workers (US-3.1)
    RUNNER_POLL_SECONDS   default 3
    RUNNER_TIMEOUT_SECONDS default 120
"""

import argparse
import os
import sys
import threading
import time
from pathlib import Path

import httpx

import provider_sim

HEARTBEAT_SECONDS = 240  # well inside the 15-minute autonomous lease

provider = provider_sim  # RUNNER_PROVIDER=claude swaps in the real CLI


def load_provider():
    """RUNNER_PROVIDER selects the provider (US-1.15); the simulator
    stays the default until the real provider is accepted."""
    name = os.environ.get("RUNNER_PROVIDER", "sim").strip().lower()
    if name == "claude":
        import provider_claude

        return provider_claude
    return provider_sim


def load_env() -> dict[str, str]:
    env = {
        "api_url": os.environ.get("FACTORY_API_URL", "http://localhost:8000"),
        "token": os.environ.get("FACTORY_WORKER_TOKEN", ""),
        "poll_seconds": os.environ.get("RUNNER_POLL_SECONDS", "3"),
        "timeout_seconds": os.environ.get("RUNNER_TIMEOUT_SECONDS", "120"),
    }
    if not env["token"]:
        # Dev convenience: share the API's .env
        api_env = Path(__file__).resolve().parents[1] / "api" / ".env"
        if api_env.exists():
            for line in api_env.read_text().splitlines():
                if line.startswith("FACTORY_WORKER_TOKEN="):
                    env["token"] = line.split("=", 1)[1].strip()
    if not env["token"]:
        sys.exit(
            "FACTORY_WORKER_TOKEN is not set — mint one on Settings → Workers"
        )
    return env


def _headers(env: dict[str, str]) -> dict[str, str]:
    return {"X-Worker-Token": env["token"]}


def _api(env: dict[str, str], method: str, path: str, **kwargs):
    resp = httpx.request(
        method,
        f"{env['api_url']}/api/v1/worker{path}",
        headers=_headers(env),
        timeout=30,
        **kwargs,
    )
    return resp


def claim_from_pool(env: dict[str, str]) -> dict | None:
    """List the pool and claim the first item; None when there is nothing
    (or every race was lost)."""
    resp = _api(env, "GET", "/pool")
    resp.raise_for_status()
    for item in resp.json()["runs"]:
        claim = _api(env, "POST", f"/runs/{item['id']}/claim")
        if claim.status_code == 200:
            return claim.json()["run"]
        if claim.status_code == 409:
            continue  # someone else took it — try the next item
        claim.raise_for_status()
    return None


def fetch_context(env: dict[str, str], run_id: str) -> dict:
    resp = _api(env, "GET", f"/runs/{run_id}/context")
    resp.raise_for_status()
    return resp.json()


def submit(env: dict[str, str], run_id: str, payload: dict) -> None:
    resp = _api(env, "POST", f"/runs/{run_id}/submit", json=payload)
    resp.raise_for_status()


def _heartbeat_loop(env: dict[str, str], run_id: str, stop: threading.Event):
    while not stop.wait(HEARTBEAT_SECONDS):
        try:
            _api(env, "POST", f"/runs/{run_id}/heartbeat")
        except Exception as e:  # noqa: BLE001 — heartbeat is best-effort
            print(f"[runner] heartbeat failed: {e}", file=sys.stderr)


def process_run(env: dict[str, str], run: dict) -> None:
    run_id = run["id"]
    bundle = fetch_context(env, run_id)
    ctx = dict(bundle.get("context") or {})
    ctx.setdefault("run_kind", bundle.get("kind", "code"))
    # the provider works the factory remote with this same worker token
    ctx["branch_name"] = bundle.get("branch_name")
    ctx["git_remote_url"] = bundle.get("git_remote_url")
    print(
        f"[runner] claimed run {run_id} kind={ctx.get('run_kind', 'code')} "
        f"({ctx.get('title')})"
    )

    stop = threading.Event()
    beat = threading.Thread(
        target=_heartbeat_loop, args=(env, run_id, stop), daemon=True
    )
    beat.start()
    try:
        result = provider.execute(
            ctx, timeout_seconds=int(env["timeout_seconds"])
        )
        if result.outcome == "succeeded":
            payload = {
                "stdout": result.stdout,
                "plan": result.plan,
                "test_plan": result.test_plan,
                "prd": result.prd,
                "stories": result.stories,
                "branch_ref": result.branch_ref,
                "pr_url": result.pr_url,
                "diff": result.diff,
                "test_cases": result.test_cases,
            }
        else:
            payload = {
                "error": result.error or "provider reported failure",
                "stdout": result.stdout,
            }
    except Exception as e:  # provider crashed — report, never vanish
        payload = {"error": f"runner exception: {e}"}
    finally:
        stop.set()

    submit(env, run_id, {k: v for k, v in payload.items() if v is not None})
    outcome = "failed" if payload.get("error") else "succeeded"
    print(f"[runner] run {run_id} -> {outcome}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="process one run, exit")
    args = parser.parse_args()

    env = load_env()
    global provider
    provider = load_provider()
    print(
        f"[runner] polling {env['api_url']} every {env['poll_seconds']}s "
        f"(provider: {provider.__name__})"
    )

    while True:
        try:
            run = claim_from_pool(env)
        except Exception as e:
            print(f"[runner] pool poll failed: {e}", file=sys.stderr)
            run = None
        if run:
            process_run(env, run)
            if args.once:
                return
        elif args.once:
            print("[runner] pool empty")
            return
        else:
            time.sleep(float(env["poll_seconds"]))


if __name__ == "__main__":
    main()
