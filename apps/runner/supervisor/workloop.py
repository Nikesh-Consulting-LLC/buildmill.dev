"""Supervisor work loop + brain (US-10.6).

Pulls work from the existing HTTP pool contract (unchanged), and for each
claimed run the brain selects the agent module for that run kind (per the
server-pushed config), injects the gateway env, drives `module.execute`, and
submits — so the review/merge pipeline is untouched. Every claimed run ends in
a submit (success or an error); a run never silently vanishes.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

import httpx

from . import modules
from .modules.base import ModuleResult, RunContext
from .primitives import LocalPrimitives

logger = logging.getLogger("supervisor.workloop")

# US-31.2: fast enough that the server's stale-claim sweep (90s of silence)
# means "the agent stopped", not "the agent is between beats". Four missed
# beats at this cadence is the server's threshold.
HEARTBEAT_SECONDS = 20

# US-31.2: the CLI's own limit stays strictly below the claim lease — the
# agent kills its own command with time left to report, rather than being
# silently replaced mid-call. Headroom covers the kill + the hand-back.
LEASE_HEADROOM_SECONDS = 60


# US-83.1: context windows measured off the CLI's own `initialize` handshake
# (its catalog declares `totalContextTokens` per model; grok-4.5 = 500000 on
# CLI 1.0.0). A BYOK `[model.*]` entry knows only what its config says, and
# auto-compact needs the window to time itself. Models absent here get no
# window written — a wrong number is worse than none. Extend deliberately,
# from measurement, never from guesswork.
KNOWN_CONTEXT_WINDOWS = {"grok-4.5": "500000"}


def model_env(
    provider_type: str, gateway_base: str, key: str, model: str, module: str = ""
) -> dict[str, str]:
    """Env a CLI module needs to reach the gateway (mirror of the server helper)."""
    base = gateway_base.rstrip("/")
    pt = (provider_type or "").lower()
    if module == "interactive":
        # US-78.5: keyed on the MODULE, not the provider type, because two
        # different programs both speak xai here — the Grok Build module below
        # runs superagent-ai/grok-cli, this one runs our fork of
        # xai-org/grok-build, and they read different variables. Mirrors
        # llm_gateway.module_env; see its comment for why each one is set.
        env = {
            "GROK_MODELS_BASE_URL": f"{base}/v1",
            "GROK_XAI_API_BASE_URL": f"{base}/v1",
            "BUILDMILL_GATEWAY_KEY": key,
            "GROK_MODEL": model,
            # US-78.5: ACP mode has no `-m` flag, so the default is the
            # only way to say which model a session reasons with.
            "GROK_DEFAULT_MODEL": model,
        }
        window = KNOWN_CONTEXT_WINDOWS.get(model)
        if window:
            env["GROK_MODEL_CONTEXT_WINDOW"] = window
        return env
    if pt == "anthropic":
        return {"ANTHROPIC_BASE_URL": base, "ANTHROPIC_API_KEY": key, "ANTHROPIC_MODEL": model}
    if pt == "xai":
        # Mirrors llm_gateway.module_env -- see its comment. Measured live
        # against the CLI's actual current generation (1.1.7): GROK_API_KEY,
        # GROK_BASE_URL (needs /v1 appended, its SDK appends
        # /chat/completions straight onto whatever base it's given), and
        # GROK_MODEL are what it reads.
        return {
            "GROK_API_KEY": key,
            "GROK_BASE_URL": f"{base}/v1",
            "GROK_MODEL": model,
        }
    if pt in ("openai", "groq"):
        return {"OPENAI_BASE_URL": base, "OPENAI_API_KEY": key, "OPENAI_MODEL": model}
    return {"LLM_BASE_URL": base, "LLM_API_KEY": key, "LLM_MODEL": model}


def subscription_mode(module: Any, config: dict[str, Any] | None) -> bool:
    """US-52.1 → US-53.1: whether this agent bills a Claude subscription.

    Reads the AGENT's own switch (`claude_billing` on the server-pushed
    config) — billing left the resolved settings in us-53.1, because whose
    money an agent spends is a property of the agent, not of a run. Still
    true only for a module that declares the `auth` knob: grok and opencode
    keep their gateway env whatever the config says.
    """
    if (config or {}).get("claude_billing") != "subscription":
        return False
    return modules.supports(module, "auth")


def subscription_env(model: str, token: str | None = None) -> dict[str, str]:
    """US-52.1: the env of a subscription run — deliberately an ABSENCE.

    Claude Code's credential chain puts ANTHROPIC_API_KEY above every
    subscription credential, so billing the subscription means delivering no
    API-key variable at all. The CLI then falls through to
    CLAUDE_CODE_OAUTH_TOKEN or the machine's own login state, both of which
    inherit from os.environ because nothing here shadows them. Only the
    factory-resolved model rides.

    US-52.2: `token` is the FACTORY-held subscription token, when the org has
    one. Injected, it wins over the machine's own credential precisely because
    the injected env merges over os.environ — the credential the manager can
    see, rotate and remove in the app is the one that runs.
    """
    env: dict[str, str] = {}
    if model:
        env["ANTHROPIC_MODEL"] = model
    if token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    return env


class ClaimLost(Exception):
    """us-96.9 AC4: the claim ended under us — a lost lease, an expiry, or a
    manager's stop. Do not boot a CLI, do not submit; release quietly."""


class WorkerClient:
    """Async client for the pool contract (US-3.2)."""

    def __init__(self, api_url: str, token: str):
        self.api_url = api_url.rstrip("/")
        self.token = token

    def _url(self, path: str) -> str:
        return f"{self.api_url}/api/v1/worker{path}"

    async def _req(self, method: str, path: str, **kw):
        async with httpx.AsyncClient(timeout=30) as c:
            return await c.request(
                method, self._url(path), headers={"X-Worker-Token": self.token}, **kw
            )

    async def list_pool(self) -> dict[str, Any]:
        r = await self._req("GET", "/pool")
        r.raise_for_status()
        return r.json()

    async def claim(self, run_id: str) -> dict[str, Any] | None:
        r = await self._req("POST", f"/runs/{run_id}/claim")
        return r.json()["run"] if r.status_code == 200 else None

    async def resume_claim(self, run_id: str) -> dict[str, Any] | None:
        """US-59.3/59.9: continue a paused/awaiting_input run this worker
        already owns — a distinct call from `claim` because the semantics
        differ (affinity-scoped, not first-come)."""
        r = await self._req("POST", f"/runs/{run_id}/resume-claim")
        return r.json()["run"] if r.status_code == 200 else None

    async def get_context(self, run_id: str) -> dict[str, Any]:
        r = await self._req("GET", f"/runs/{run_id}/context")
        r.raise_for_status()
        return r.json()

    async def submit(self, run_id: str, payload: dict[str, Any]):
        return await self._req("POST", f"/runs/{run_id}/submit", json=payload)

    async def release(self, run_id: str, note: str = ""):
        """Give a claimed run back to the pool (US-31.1: a hand-back that
        could not be delivered must not sit out the lease)."""
        return await self._req("POST", f"/runs/{run_id}/release", json={"note": note})

    async def heartbeat(self, run_id: str):
        try:
            await self._req("POST", f"/runs/{run_id}/heartbeat")
        except Exception:  # noqa: BLE001 — heartbeat is best-effort
            pass

    async def claim_alive(self, run_id: str) -> tuple[bool, str | None]:
        """us-96.9 AC4: is this claim still ours to work? Answered by the
        heartbeat endpoint, which 409s when there is no live claim to
        extend. A network blip answers True — killing a healthy run over a
        dropped packet is worse than a wasted boot — so only the server
        SAYING the claim is gone counts."""
        try:
            r = await self._req("POST", f"/runs/{run_id}/heartbeat")
        except Exception:  # noqa: BLE001 — benefit of the doubt
            return True, None
        if r.status_code in (404, 409):
            detail = ""
            try:
                detail = (r.json() or {}).get("detail") or ""
            except Exception:  # noqa: BLE001
                pass
            return False, detail or f"the server answered {r.status_code}"
        return True, None

    # US-63.x follow-up: release-prep is deliberately its own three-endpoint
    # contract (apps/api/app/routers/worker.py), not the /runs/* surface — a
    # release prep has no issue, no lease/resume machinery, and its
    # submission goes through submit_release_notes over MCP, not here.

    async def list_release_prep(self) -> dict[str, Any]:
        r = await self._req("GET", "/release-prep")
        r.raise_for_status()
        return r.json()

    async def claim_release_prep(self, prep_id: str) -> dict[str, Any] | None:
        r = await self._req("POST", f"/release-prep/{prep_id}/claim")
        return r.json() if r.status_code == 200 else None

    async def release_prep_heartbeat(self, prep_id: str):
        try:
            await self._req("POST", f"/release-prep/{prep_id}/heartbeat")
        except Exception:  # noqa: BLE001 — heartbeat is best-effort
            pass

    async def release_prep_status(self, prep_id: str) -> str | None:
        try:
            r = await self._req("GET", f"/release-prep/{prep_id}")
            if r.status_code == 200:
                return r.json().get("status")
        except Exception:  # noqa: BLE001
            pass
        return None

    async def fail_release_prep(self, prep_id: str, error: str):
        """Best-effort: a release prep this worker could not deliver must not
        sit 'running' forever — mirrors `release()` giving a run back."""
        try:
            await self._req(
                "POST", f"/release-prep/{prep_id}/submit",
                json={"notes_summary": "", "notes_detail": "", "error": error},
            )
        except Exception:  # noqa: BLE001
            pass


# US-31.9: run kinds that go through the MCP hand-back path. `prd` and
# `breakdown` answer in their stdout and need no repository, so they are
# unaffected by whether a module can be given MCP.
# US-43.7: `test`, `guidelines` and `elaborate` join them — each reads the
# repository over the factory's MCP tools and hands back with a submit tool, so
# a module that cannot be given an MCP config cannot do them either. `prd` and
# `breakdown` stay out for the reason above: they answer from context.
MCP_REQUIRED_KINDS = {"code", "plan", "test", "guidelines", "elaborate"}


def module_can_do(name: str, kind: str) -> tuple[bool, str | None]:
    """Whether this module can take this kind of run, and why not if it can't.

    US-31.9: `code` and `plan` runs work through the factory's MCP tools now,
    so a module that cannot be handed an MCP config cannot take them — and it
    SAYS which condition it fails rather than silently falling back to a git
    path this story deleted (the us-27.9 rule). US-32.4 generalizes this into
    a per-setting declaration; MCP is the first one.
    """
    m = modules.get(name)
    if not m:
        return False, f"'{name}' is not a module this runner has"
    if kind not in m.capabilities:
        return False, f"the {name} module does not do '{kind}' work"
    # The requirement applies to modules that work in a real checkout. `sim`
    # fabricates its results and opens no repository, so it is unaffected —
    # otherwise this would disqualify the module whose whole job is proving
    # the pipeline without an agent.
    if (
        kind in MCP_REQUIRED_KINDS
        and getattr(m, "needs_repo", True)
        and not getattr(m, "supports_mcp", False)
    ):
        return False, (
            f"the {name} module cannot be given an MCP server config, and "
            f"'{kind}' runs work through the factory's MCP tools — enable a "
            "module that supports MCP (claude)"
        )
    return True, None


def kind_enabled(config: dict[str, Any] | None, kind: str) -> bool:
    """US-53.4: whether this agent's kind checkboxes allow the work.

    Null/absent means ALL kinds — a config written before the checkboxes
    existed keeps today's behavior. A list (empty included) is the manager's
    explicit choice."""
    kinds = (config or {}).get("enabled_kinds")
    if kinds is None:
        return True
    return kind in kinds


def select_module(config: dict[str, Any], kind: str) -> str | None:
    """Pick the module for a run kind: the configured `module_routes[kind]` when
    enabled and capable, else the first enabled module that can do it."""
    # US-53.4: an unchecked kind is refused before any module is consulted —
    # the run stays in the pool for an agent that does it.
    if not kind_enabled(config, kind):
        return None
    enabled = config.get("enabled_modules") or []
    routes = config.get("module_routes") or {}
    preferred = routes.get(kind)
    if preferred and preferred in enabled:
        ok, _why = module_can_do(preferred, kind)
        if ok:
            return preferred
    for name in enabled:
        ok, _why = module_can_do(name, kind)
        if ok:
            return name
    return None


def select_release_prep_module(config: dict[str, Any]) -> str | None:
    """Release prep is supervisor infrastructure, not story work a manager
    routes or gates per kind — it has no entry in `runs_kind_check` (it never
    becomes a `runs` row) and deliberately no `enabled_kinds`/`module_routes`
    checkbox of its own (test_the_runner_declares_no_kind_the_database_rejects
    pins `HANDBACK_SHAPE` to that constraint, so it must stay out of it). Any
    enabled module that can be given an MCP config can do it — that is the
    whole requirement, since the job is entirely claim/read/submit over the
    factory's release-prep MCP tools.
    """
    for name in config.get("enabled_modules") or []:
        m = modules.get(name)
        if m is not None and getattr(m, "supports_mcp", False):
            return name
    return None


def why_no_module(config: dict[str, Any], kind: str) -> str:
    """US-31.9 / us-27.9: an agent that cannot work says which condition it
    fails — per enabled module, not just "no module can do this"."""
    # US-53.4: the checkbox refusal names the setting, so the manager reads
    # "unchecked" and not a mystery about modules.
    if not kind_enabled(config, kind):
        return (
            f"this agent does not do '{kind}' work — it is unchecked in the "
            "agent's settings"
        )
    enabled = config.get("enabled_modules") or []
    if not enabled:
        return (
            f"no enabled module can do '{kind}' work: none are enabled on "
            "this agent"
        )
    reasons = []
    for name in enabled:
        _ok, why = module_can_do(name, kind)
        if why:
            reasons.append(why)
    return (
        f"no enabled module can do '{kind}' work: " + "; ".join(reasons)
        if reasons
        else f"no enabled module can do '{kind}' work"
    )


def undeliverable_settings(module: Any, resolved: dict[str, Any] | None) -> list[str]:
    """US-32.4: resolved settings the chosen module cannot express.

    A run that was told to think harder, didn't, and reported nothing is the
    failure this prevents. The module's own declaration is the authority — so a
    module that grows a knob stops appearing in this list without anything else
    changing.
    """
    name = getattr(module, "name", "?")
    out: list[str] = []
    for key, value in sorted((resolved or {}).items()):
        # An unset setting was never asked for; only a value can go missing.
        if value in (None, "", [], {}):
            continue
        if key not in modules.KNOWN_SETTINGS:
            out.append(
                f"'{key}' is not a setting any module understands, so it was "
                "not delivered"
            )
        elif not modules.supports(module, key):
            out.append(
                f"the {name} module cannot be told '{key}', so that setting "
                "was not delivered"
            )
    return out


# US-36.1: the values `run_trace_kind_check` permits. Anything else is
# rejected by Postgres, and before us-36.1 that took the control socket down
# with it. US-39.1 is the first thing to send more than one of them.
TRACE_KINDS = (
    "step",
    "tool",
    "decision",
    "output",
    "progress",
    "clarification",
    "submission",
    "error",
)


def timeout_from_lease(
    lease_seconds: Any, max_minutes: Any = None
) -> int | None:
    """US-31.2: derive the CLI limit from the lease, strictly below it.
    None when the bundle carries no lease (an older server).

    US-33.2: a preset's own time ceiling NARROWS this and can never widen it.
    us-31.2 owns the lease and the invariant that the agent's work limit stays
    strictly below it; a preset that could raise the limit past the lease would
    produce a run allowed to outlive its own claim. An unset ceiling changes
    nothing and the lease-derived value stands.
    """
    try:
        lease = int(lease_seconds)
    except (TypeError, ValueError):
        lease = 0
    derived: int | None = None
    if lease > 0:
        # 90% of the lease, but always leaving the headroom; the floor of 30s
        # keeps a tiny lease usable while never reaching the lease itself.
        derived = max(30, min(int(lease * 0.9), lease - LEASE_HEADROOM_SECONDS))
    try:
        preset_seconds = int(max_minutes) * 60 if max_minutes else 0
    except (TypeError, ValueError):
        preset_seconds = 0
    if preset_seconds > 0:
        preset_seconds = max(30, preset_seconds)
        # min(), never max(): narrowing only.
        derived = preset_seconds if derived is None else min(derived, preset_seconds)
    return derived


def build_run_context(bundle: dict[str, Any], env: dict[str, str] | None = None) -> RunContext:
    # US-31.8: the project id decides the workspace folder. It rides in the
    # bundle's top level; mirror it into the context the modules read so
    # both `workspace_for` callers see the same value.
    context = dict(bundle.get("context") or {})
    if bundle.get("project_id") and not context.get("project_id"):
        context["project_id"] = bundle["project_id"]
    resolved = dict(bundle.get("run_settings") or {})
    timeout = timeout_from_lease(
        bundle.get("lease_seconds"), resolved.get("max_minutes")
    )
    if timeout is None:
        # Older server: keep today's default and say so once in the log.
        logger.info(
            "work-context bundle carries no lease; keeping the default "
            "1200s CLI limit"
        )
    return RunContext(
        run_id=str(bundle.get("run_id")),
        kind=bundle.get("kind", "code"),
        context=context,
        branch_name=bundle.get("branch_name"),
        git_remote_url=bundle.get("git_remote_url"),
        default_branch=bundle.get("default_branch") or "main",
        model_env=env or {},
        # US-32.8: what the factory resolved for this run. Absent from an older
        # server's bundle, which is simply an empty tuning set.
        settings=resolved,
        tool_servers=list(bundle.get("tool_servers") or []),
        tool_notes=list(bundle.get("tool_notes") or []),
        # US-59.3: null on an ordinary claim; the id to `--resume` when the
        # server claimed this run back onto the worker that already holds
        # its matching local transcript and workspace.
        resume_session_id=bundle.get("resume_session_id"),
        **({"timeout_seconds": timeout} if timeout is not None else {}),
    )


def sanitize_payload(value: Any) -> Any:
    """Strip NUL bytes from every string in a payload, recursively.

    US-31.1: Postgres text cannot carry 0x00 and psycopg refuses it
    client-side, so one NUL anywhere in a CLI's output turned a failure
    report into an HTTP 500 — and the failure into a silent lease loop.
    The API sanitizes too; the runner does it first so the report is
    deliverable even against an older server."""
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {k: sanitize_payload(v) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_payload(v) for v in value]
    return value


# Backoff between hand-back attempts. Short: the run holds its claim while
# we retry, so patience here is paid for in lease time.
SUBMIT_RETRY_DELAYS = (2, 5, 15)

# US-42.2: how many distinct field errors the summary names before it stops
# counting. Three is enough to recognise the shape of a refusal; the raw body
# follows it for anyone who needs the rest.
REFUSAL_SUMMARY_FIELDS = 3
REFUSAL_RAW_CHARS = 600


def _clip(text: str, limit: int) -> str:
    """Truncate on a boundary. The 2026-07-28 incidents were cut mid-token
    (`Input should be a vali`), which reads as corruption rather than as a
    message that continues."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    if space > limit // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:") + "…"


def _field_path(loc: Any) -> str:
    """`["body","test_cases",0,"steps"]` -> `test_cases[].steps`.

    Indices are collapsed on purpose: a body-validation error repeats itself
    once per list element, so the 2026-07-28 refusal carried the same message
    fifteen times over. Collapsed, fifteen lines become one fact."""
    parts = [p for p in (loc or []) if p != "body"]
    if not parts:
        return "body"
    out = ""
    for part in parts:
        if isinstance(part, int):
            out += "[]"
        else:
            out = f"{out}.{part}" if out else str(part)
    return out


def describe_refusal(status: int | None, body: str) -> str:
    """US-42.2: an incident message a manager can read.

    A refused hand-back used to record 400 characters of raw Pydantic error
    array, cut mid-token, naming neither the call nor legibly the field. The
    manager reading the 2026-07-28 batch could tell that *something* was
    refused and nothing else. Lead with the fields; keep the body behind them.
    """
    code = status if status is not None else "no response"
    summary = ""
    try:
        parsed = json.loads(body or "")
    except (ValueError, TypeError):
        parsed = None
    detail = parsed.get("detail") if isinstance(parsed, dict) else None
    complete = False
    if isinstance(detail, str):
        # The server said it in one sentence; the raw body adds nothing.
        summary, complete = detail, True
    elif isinstance(detail, list):
        counts: dict[str, int] = {}
        for item in detail:
            if not isinstance(item, dict):
                continue
            msg = str(item.get("msg") or "").strip()
            field = _field_path(item.get("loc"))
            key = f"{field} — {msg}" if msg else field
            counts[key] = counts.get(key, 0) + 1
        named = list(counts.items())[:REFUSAL_SUMMARY_FIELDS]
        parts = [f"{k} (x{n})" if n > 1 else k for k, n in named]
        if len(counts) > REFUSAL_SUMMARY_FIELDS:
            parts.append(f"+{len(counts) - REFUSAL_SUMMARY_FIELDS} more")
        summary = "; ".join(parts)
    if not summary:
        summary = _clip(body, 200) or "no detail"
    line = f"submit refused ({code}): {summary}"
    if complete:
        return line
    raw = _clip(body, REFUSAL_RAW_CHARS)
    return f"{line}\nraw: {raw}" if raw and raw not in line else line


def result_to_payload(r: ModuleResult) -> dict[str, Any]:
    if r.outcome != "succeeded":
        from .repair import classify_fault, turn_limit_hit

        if getattr(r, "stopped", False):
            # us-96.9: a manager stop carries NO fault_class — neither the
            # box nor the story failed. The server lands it as `stopped`
            # (the run row already carries stopped_reason), consuming no
            # attempt and writing no agent-failure row.
            return {
                k: v
                for k, v in {
                    "error": "stopped by the manager",
                    "stdout": r.stdout,
                    "claude_session_id": r.claude_session_id,
                }.items()
                if v is not None
            }
        return {
            k: v
            for k, v in {
                "error": r.error or "module failed",
                "stdout": r.stdout,
                "fault_class": classify_fault(r),
                # US-59.1: rides every hand-back, not only a success.
                "claude_session_id": r.claude_session_id,
                # US-59.3: the one signal that tells the server this
                # non-success submit is a turn-limit exit, which should
                # pause rather than fail outright. `turn_limit_hit` already
                # exists as the string-match predicate repair.py uses to
                # decide NOT to retry within this claim — reused here rather
                # than re-matching the same text a second way.
                "pause_reason": "turn_limit" if turn_limit_hit(r) else None,
            }.items()
            if v is not None
        }
    payload = {
        "plan": r.plan,
        "test_plan": r.test_plan,
        "prd": r.prd,
        "stories": r.stories,
        "branch_ref": r.branch_ref,
        "pr_url": r.pr_url,
        "diff": r.diff,
        "test_cases": r.test_cases,
        "stdout": r.stdout,
        "tokens_in": r.tokens_in,
        "tokens_out": r.tokens_out,
        "claude_session_id": r.claude_session_id,
    }
    return {k: v for k, v in payload.items() if v is not None}


# US-32.8: the resolved settings ride along, because the model the gateway keys
# on is one of them now rather than a raw `model_routes` lookup.
EnvProvider = Callable[[str, str, Any, dict], Awaitable["dict[str, str] | None"]]


class Supervisor:
    """The brain loop over the pool. `config_provider` returns the live config;
    `env_provider` mints the gateway env for a module (optional)."""

    def __init__(
        self,
        client: WorkerClient,
        config_provider: Callable[[], dict[str, Any]],
        env_provider: EnvProvider | None = None,
        connection: Any = None,
        poll_seconds: float = 3,
    ):
        self.client = client
        self.config_provider = config_provider
        self.env_provider = env_provider
        self.connection = connection  # RunnerConnection, for the shell auditor
        self.poll_seconds = poll_seconds

    async def run_claimed(self, run_id: str) -> ModuleResult | None:
        """Supervise one already-claimed run to a submit. Never vanishes."""
        try:
            bundle = await self.client.get_context(run_id)
        except Exception as e:  # noqa: BLE001
            await self._hand_back(run_id, {"error": f"could not fetch context: {e}"})
            return None
        kind = bundle.get("kind", "code")
        config = self.config_provider() or {}
        name = select_module(config, kind)
        if not name:
            # US-31.9: name the condition, not just the outcome.
            reason = why_no_module(config, kind)
            logger.warning("run %s cannot be worked: %s", run_id, reason)
            await self._hand_back(
                run_id, {"error": reason, "fault_class": "runner-fault"}
            )
            return None
        module = modules.get(name)

        resolved = dict(bundle.get("run_settings") or {})
        env: dict[str, str] = {}
        if self.env_provider is not None:
            try:
                env = await self.env_provider(run_id, kind, module, resolved) or {}
            except Exception as e:  # noqa: BLE001
                # US-36.1: this used to log a warning and run the module anyway.
                # A CLI started with no key does not report "I have no key" — it
                # reports `Not logged in · Please run /login`, which reads as a
                # broken machine and sends the manager to the agent box, where
                # nothing is wrong. On 2026-07-27 that turned a dead control
                # socket into five runs that each failed in four seconds with a
                # reason that named the wrong thing entirely.
                #
                # A module that needs credentials and has none must not start.
                if getattr(module, "provider_type", ""):
                    # The wording is load-bearing: it must not contain the
                    # phrase the CLI would have printed, even to explain it.
                    # A manager skimming an error and seeing those words goes
                    # to the machine — which is the failure this fixes.
                    reason = (
                        f"could not obtain model credentials for this run: {e}. "
                        "The agent was NOT started, so nothing ran on its "
                        "machine and nothing there needs fixing. This is a "
                        "factory-side fault between the agent and the LLM "
                        "gateway; dispatch the item again once the agent's "
                        "control socket is healthy."
                    )
                    logger.error("run %s: %s", run_id, reason)
                    self._trace(run_id, reason)
                    await self._raise_incident(run_id, reason)
                    await self._hand_back(
                        run_id, {"error": reason, "fault_class": "runner-fault"}
                    )
                    return ModuleResult(outcome="failed", error=reason)
                # A module with no provider_type (`sim`) legitimately needs no
                # gateway; it is not refused.
                logger.warning("gateway env unavailable (%s); running without it", e)

        ctx = build_run_context(bundle, env)

        # US-32.4: if the settings resolved for this run name something this
        # module cannot express, say so before it runs — on the run trace, and
        # again in the hand-back, so it survives even if the trace does not.
        undelivered = undeliverable_settings(module, bundle.get("run_settings"))
        for line in undelivered:
            logger.warning("run %s: %s", run_id, line)
            self._trace(run_id, line)

        auditor = None
        if self.connection is not None:
            from .audit import SocketAuditor

            auditor = SocketAuditor(self.connection, run_id)
        # US-89.2: the project's defined environment (the manager's per-project
        # entries, resolved server-side with agent-scoped overrides applied)
        # becomes REAL process env for the CLI and everything it spawns.
        # Gateway/model vars win on a name collision — the factory's own
        # wiring is not overridable from a project entry.
        run_env = {**(bundle.get("environment") or {}), **(env or {})}
        prim = LocalPrimitives(
            env=run_env,
            audit=auditor.audit if auditor else None,
            report=auditor.report if auditor else None,
        )
        # US-39.1: what the agent is doing, as it does it. The module narrates
        # to its own logger regardless (the machine's console); this sink is
        # what puts the same lines on the run page while the run is still going.
        setter = getattr(module, "set_progress_sink", None)
        if callable(setter):
            def progress(kind: str, line: str, _run=run_id) -> None:
                self._trace(_run, line, kind)

            try:
                setter(progress)
            except Exception:  # noqa: BLE001 — narration is never fatal
                logger.debug("could not attach the progress sink", exc_info=True)

        policy = config.get("autonomy_policy") or {}
        max_attempts = int(policy.get("max_repair_attempts", 2))
        diagnose = self._diagnoser() if self.connection is not None else None

        stop = asyncio.Event()
        beat = asyncio.create_task(self._heartbeat(run_id, stop))
        try:
            from .repair import execute_with_repair

            # US-27.12: each repair attempt goes in the run trace — what it
            # changed and what happened. Two attempts that changed nothing
            # and failed identically were invisible on 2026-07-26.
            def note_attempt(n, action, changed, outcome):
                self._trace(
                    run_id,
                    f"repair attempt {n}: {action} — "
                    + (changed or "nothing to change")
                    + f" -> {outcome}",
                )

            # us-96.9 AC4: no boot without a live claim — first attempt or
            # after any repair action. The 2026-08-14 zombie (a fresh CLI
            # booted after a reclone, on a claim the manager's stop had
            # already ended, probing the factory with a revoked token) is
            # structurally impossible with this check in front of every
            # invocation.
            async def preflight():
                alive, why = await self.client.claim_alive(run_id)
                if not alive:
                    raise ClaimLost(why or "no live claim on this run")

            result = await execute_with_repair(
                module,
                ctx,
                prim,
                max_attempts=max_attempts,
                diagnose=diagnose,
                on_attempt=note_attempt,
                preflight=preflight,
            )
        except ClaimLost as e:
            # One trace line naming why, then release quietly — a submit
            # against a claim we no longer hold would only 409.
            self._trace(
                run_id, f"claim lost before boot — {e}; releasing quietly"
            )
            stop.set()
            beat.cancel()
            try:
                await self.client.release(run_id, f"claim lost before boot: {e}")
            except Exception:  # noqa: BLE001 — the claim is already gone
                pass
            return None
        except Exception as e:  # noqa: BLE001 — report, never vanish
            result = ModuleResult(outcome="failed", error=f"module crashed: {e}")
        finally:
            stop.set()
            beat.cancel()
        payload = result_to_payload(result)
        if undelivered:
            # Appended to the run's own record rather than replacing anything:
            # a run that succeeded while quietly ignoring half its tuning is
            # still a run whose manager needs to know.
            payload["settings_not_delivered"] = undelivered
        accepted = await self._hand_back(run_id, payload)
        if accepted:
            # US-31.1: this line may only print an outcome the server accepted.
            logger.info("run %s -> %s (%s)", run_id, result.outcome, name)
        return result

    async def run_release_prep(self, item: dict[str, Any]) -> None:
        """Supervise one queued release-prep job to a submit — the missing
        consumer: the job sat `queued` forever because nothing ever claimed
        it (release_prep_runs has its own claim/heartbeat/submit contract,
        deliberately off the /runs/* pool this loop otherwise polls).

        The agent's own `submit_release_notes` MCP call is what actually
        completes the job. A CLI that exits 0 without making that call would
        leave the release prep silently stuck `running` — the same failure
        this method exists to fix, in a new shape — so success is verified
        against the server's own status, not trusted from the exit code.
        """
        prep_id = item.get("id")
        if not prep_id:
            return
        claimed = await self.client.claim_release_prep(prep_id)
        if not claimed:
            return  # lost the race to another worker; not an error
        config = self.config_provider() or {}
        name = select_release_prep_module(config)
        if not name:
            logger.warning(
                "release prep %s cannot be worked: no enabled module can be "
                "given an MCP server config", prep_id,
            )
            await self.client.fail_release_prep(
                prep_id,
                "no enabled module on this agent can be given an MCP server "
                "config, and release-prep work happens entirely through the "
                "factory's MCP tools",
            )
            return
        module = modules.get(name)

        # US-63.x follow-up: `release_prep` has no `run_routes`/`model_routes`
        # entry of its own (it is not a ROUTE_KINDS member — see
        # select_release_prep_module), so passing empty resolved settings left
        # the env provider's `model` fall through to "" and, from there, to
        # whatever the org default happened to be — not this agent's own
        # configured model. Reuse the same model this agent already uses for
        # its other kinds, preferring the closest analog (`release`, `code`),
        # so this job runs under a model the manager actually picked.
        overrides = config.get("model_overrides") or {}
        model = (
            overrides.get("release_prep")
            or overrides.get("release")
            or overrides.get("code")
            or next(iter(overrides.values()), None)
        )

        env: dict[str, str] = {}
        if self.env_provider is not None:
            try:
                resolved = {"model": model} if model else {}
                env = await self.env_provider(prep_id, "release_prep", module, resolved) or {}
            except Exception as e:  # noqa: BLE001 — mirrors run_claimed's own gate
                if getattr(module, "provider_type", ""):
                    reason = (
                        f"could not obtain model credentials for this release "
                        f"prep: {e}. The agent was NOT started."
                    )
                    logger.error("release prep %s: %s", prep_id, reason)
                    await self.client.fail_release_prep(prep_id, reason)
                    return
                logger.warning("gateway env unavailable (%s); running without it", e)

        ctx = RunContext(
            run_id=prep_id,
            kind="release_prep",
            context={
                "prep_id": prep_id,
                "version": item.get("version") or "",
                "project_id": item.get("project_id"),
            },
            model_env=env,
        )
        auditor = None
        if self.connection is not None:
            from .audit import SocketAuditor

            auditor = SocketAuditor(self.connection, prep_id)
        prim = LocalPrimitives(
            env=env,
            audit=auditor.audit if auditor else None,
            report=auditor.report if auditor else None,
        )

        stop = asyncio.Event()
        beat = asyncio.create_task(self._release_prep_heartbeat(prep_id, stop))
        try:
            result = await module.execute(ctx, prim)
        except Exception as e:  # noqa: BLE001 — report, never vanish
            result = ModuleResult(outcome="failed", error=f"module crashed: {e}")
        finally:
            stop.set()
            beat.cancel()

        if result.outcome != "succeeded":
            await self.client.fail_release_prep(
                prep_id, result.error or "release prep failed"
            )
            return

        status = await self.client.release_prep_status(prep_id)
        if status not in ("succeeded", "failed"):
            # The CLI exited clean but never called submit_release_notes —
            # exactly the silent-vanish failure mode this loop exists to close.
            await self.client.fail_release_prep(
                prep_id,
                "the agent finished without calling submit_release_notes",
            )

    async def _release_prep_heartbeat(self, prep_id: str, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                await self.client.release_prep_heartbeat(prep_id)

    async def _hand_back(self, run_id: str, payload: dict[str, Any]) -> bool:
        """Deliver a hand-back, or say loudly that it could not be delivered.

        US-31.1: on 2026-07-26 two agents had their failure reports refused
        with a 500, logged `run -> failed` anyway, and went back to polling.
        The runs sat `running` until their leases expired — four times over.
        A refused hand-back is now retried with backoff; one that still
        cannot land is logged as REFUSED (never as an outcome), the run is
        released back to the pool immediately, and an incident is raised so
        the manager sees it in the app rather than in a journal."""
        payload = sanitize_payload(payload)
        status: int | None = None
        detail = ""
        for i, delay in enumerate((0,) + SUBMIT_RETRY_DELAYS):
            if delay:
                await asyncio.sleep(delay)
            try:
                r = await self.client.submit(run_id, payload)
                status = r.status_code
                if r.status_code < 300:
                    return True
                # US-42.2: kept whole enough to still parse as JSON — clipping
                # to 400 here is what left the incident an unreadable fragment.
                detail = (r.text or "")[:4000]
                # 4xx (bar the retryable pair) is a final answer: the server
                # understood us and said no. Retrying it is just louder.
                if r.status_code < 500 and r.status_code not in (408, 429):
                    break
            except Exception as e:  # noqa: BLE001 — transport counts as retryable
                status, detail = None, str(e)[:4000]
            logger.warning(
                "hand-back for run %s not delivered (attempt %d, %s); retrying",
                run_id, i + 1, status if status is not None else "transport error",
            )
        logger.error(
            "hand-back for run %s REFUSED (%s): %s — releasing the run",
            run_id, status if status is not None else "no response",
            _clip(detail, 400),
        )
        self._trace(
            run_id,
            f"hand-back refused ({status if status is not None else 'no response'}); "
            "run released back to the pool",
        )
        # US-42.2: named, not dumped — the run page shows this verbatim.
        await self._raise_incident(run_id, describe_refusal(status, detail))
        try:
            await self.client.release(run_id, note="hand-back could not be delivered")
        except Exception as e:  # noqa: BLE001 — the lease reclaim is the backstop
            logger.warning("release after refused hand-back failed too: %s", e)
        return False

    async def _raise_incident(self, run_id: str, message: str) -> None:
        """Best-effort runner incident over the control socket (US-31.1)."""
        if self.connection is None:
            return
        try:
            await self.connection.notify(
                "runner.incident",
                {"run_id": run_id, "kind": "runner-fault", "message": message[:2000]},
            )
        except Exception:  # noqa: BLE001
            pass

    def _trace(self, run_id: str, content: str, kind: str = "progress") -> None:
        """Append one line to the run's trace over the control socket.

        Fire-and-forget: the supervisor must never lose a run because it could
        not narrate one."""
        if self.connection is None:
            return
        if kind not in TRACE_KINDS:
            # US-36.1 again, from the other side: the server clamps an unknown
            # kind, but sending one it must clamp is still a bug worth not
            # writing. US-39.1 introduced the first caller that passes a kind.
            kind = "progress"
        try:
            asyncio.create_task(
                self.connection.notify(
                    "run.trace",
                    # US-36.1: send a kind the server's constraint actually
                    # permits. Sending none let the server default to `note`,
                    # which `run_trace_kind_check` rejects — so every trace this
                    # runner ever wrote raised, and took the socket with it.
                    {"run_id": run_id, "kind": kind, "content": content},
                )
            )
        except Exception:  # noqa: BLE001
            pass

    def _diagnoser(self):
        """A best-effort brain diagnosis of a failure (US-10.3 relay)."""

        async def diagnose(result: ModuleResult):
            try:
                # US-27.12: the brain used to be handed `result.error` alone —
                # which was the string "CLI failed or timed out". Asked why a
                # command "failed or timed out", it answered that it had timed
                # out, and that invention reached the incident and the
                # notification. Give it the facts, and tell it not to guess.
                facts = f"exit code: {result.exit_code}"
                if result.duration_seconds is not None:
                    facts += f"; ran for {result.duration_seconds:.0f}s"
                tail = (result.stdout or "").strip()[-1500:]
                reply = await self.connection.infer(
                    [
                        {
                            "role": "user",
                            "content": (
                                "A coding-agent CLI failed. In one sentence: why, "
                                "and how would you fix it? Use ONLY what is below. "
                                "If it does not say, answer 'the output does not "
                                "say'. Do not guess at timeouts, networks or "
                                "permissions.\n\n"
                                f"{facts}\n\n{(result.error or '')[:800]}\n\n"
                                f"Command output:\n{tail}"
                            ),
                        }
                    ],
                    route="runner_brain",
                    timeout=30,
                )
                return reply.get("completion")
            except Exception:  # noqa: BLE001
                return None

        return diagnose

    async def _heartbeat(self, run_id: str, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                try:
                    await self.client.heartbeat(run_id)
                except Exception:  # noqa: BLE001 — a missed beat is not fatal.
                    # A factory-api restart fails whichever beat lands mid-outage;
                    # letting that exception escape used to end the whole task,
                    # which silently stopped every heartbeat for the rest of the
                    # run and guaranteed the server's 90s stale-claim sweep would
                    # call it stuck even seconds after the API came back. Missing
                    # one beat and trying again next tick rides out exactly the
                    # kind of blip execute_with_repair's "wait" budget (repair.py)
                    # is now built to survive.
                    logger.debug(
                        "heartbeat for run %s failed; retrying next beat", run_id
                    )

    async def supervise(self, stop: asyncio.Event | None = None, once: bool = False) -> None:
        while stop is None or not stop.is_set():
            if self.connection is not None:
                # US-31.1: evidence stranded by a socket drop lands on the
                # next idle beat, not only when the next command runs.
                from .audit import flush_pending

                try:
                    await flush_pending(self.connection)
                except Exception:  # noqa: BLE001
                    pass
            # US-78.10 AC3: a session holds this agent for its life. Claiming a
            # run while one is open would put two conversations in one
            # workspace, editing the same files with no idea about each other.
            # Checked before the poll so a held agent is not even offered work.
            from . import session_host

            if session_host.is_busy():
                if once:
                    return
                await asyncio.sleep(self.poll_seconds)
                continue
            try:
                pool = await self.client.list_pool()
            except Exception as e:  # noqa: BLE001
                logger.warning("pool poll failed: %s", e)
                pool = {"runs": []}
            claimed_id = None
            # US-59.9: this worker's own parked runs come first — resuming
            # them before pulling fresh work is what stops a paused run
            # waiting behind an endless stream of new items landing on the
            # same machine, which a plain FIFO over `runs` alone would do.
            for item in pool.get("resumable", []):
                run = await self.client.resume_claim(item["id"])
                if run:
                    claimed_id = run.get("id") or item["id"]
                    break
            if not claimed_id:
                for item in pool.get("runs", []):
                    run = await self.client.claim(item["id"])
                    if run:
                        claimed_id = run.get("id") or item["id"]
                        break
            if claimed_id:
                await self.run_claimed(claimed_id)
                if once:
                    return
                continue

            # US-63.x follow-up: the pool has nothing — check the separate
            # release-prep queue before idling. Low volume and off the /runs
            # surface on purpose, so it rides its own poll rather than being
            # folded into `pool`.
            prep_item = None
            try:
                prep_pool = await self.client.list_release_prep()
                items = prep_pool.get("items") or []
                prep_item = items[0] if items else None
            except Exception as e:  # noqa: BLE001
                logger.warning("release-prep poll failed: %s", e)
            if prep_item:
                await self.run_release_prep(prep_item)
                if once:
                    return
                continue

            if once:
                return
            await asyncio.sleep(self.poll_seconds)
