"""Ingestion for what a deployed app reports about itself (US-16.2).

Everything here runs *outside* a Supabase session: a deployed app has no JWT,
only its deployment's report key. So this module owns the whole trust boundary
— authenticate the key, resolve org/project from the deployment row rather
than from anything the caller said, cap what a stranger can store, and cap how
often. `app_issues` has no insert policy; this service-role write is the only
way a row is ever created.

US-16.8 calls `ingest_report` directly for Build Mill's own errors, which is
why the work lives here rather than in the router.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import time
import traceback
from collections import defaultdict, deque
from typing import Any

from .config import Settings
from .db import _connect, _valid_uuid

logger = logging.getLogger(__name__)

# Caps. Oversized input is truncated rather than rejected: a legitimate 2MB
# stack trace should still record *something* a manager can act on, and a
# stranger's report is not worth a 413 round trip.
TITLE_LIMIT = 300
MESSAGE_LIMIT = 8_000
STACK_LIMIT = 20_000
CONTEXT_LIMIT = 8_000
REPORTER_LIMIT = 200

# Per-deployment rate limit. Generous enough for a real incident's burst —
# a crash loop firing every second stays under it — and tight enough that a
# misbehaving client cannot fill the table. In-process and per-worker: the
# API runs as one process, and the backstop that actually bounds table growth
# is fingerprint dedup, not this.
RATE_LIMIT_PER_MINUTE = 120
_RATE_WINDOW_SECONDS = 60.0
_rate_buckets: dict[str, deque[float]] = defaultdict(deque)


class RateLimited(Exception):
    """Raised by `ingest_report` when a deployment is over its per-minute cap."""


def _truncate(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    text = text.strip()
    if not text:
        return None
    if len(text) <= limit:
        return text
    # Say so in the stored value — a silently cut stack trace reads as a
    # complete one that happens to end mid-frame.
    return text[:limit] + f"\n… [truncated at {limit} characters]"


def _truncate_context(context: Any) -> dict[str, Any]:
    """`context` is a free-form bag, so it is the easiest way to make a row
    unboundedly large. Keep it a JSON object and keep it small; a caller that
    sends something else gets it recorded under a key rather than dropped."""
    if context is None:
        return {}
    if not isinstance(context, dict):
        context = {"value": context}
    encoded = json.dumps(context, default=str)
    if len(encoded) <= CONTEXT_LIMIT:
        return json.loads(encoded)
    # Drop the largest values until it fits rather than truncating the JSON
    # text, which would store something that no longer parses.
    trimmed = dict(json.loads(encoded))
    for key in sorted(trimmed, key=lambda k: len(json.dumps(trimmed[k], default=str)), reverse=True):
        trimmed[key] = _truncate(json.dumps(trimmed[key], default=str), 500)
        if len(json.dumps(trimmed, default=str)) <= CONTEXT_LIMIT:
            break
    if len(json.dumps(trimmed, default=str)) > CONTEXT_LIMIT:
        return {"_truncated": "context exceeded the size cap and was dropped"}
    return trimmed


# Variable content that makes two occurrences of one crash look like two
# crashes. Normalizing before hashing is what keeps a crash loop with an
# embedded timestamp on one row; it is a mitigation, not a solution — the
# design doc says so outright.
_NOISE = [
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<uuid>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"), "<timestamp>"),
    (re.compile(r"\b0x[0-9a-f]{4,}\b", re.I), "<address>"),
    (re.compile(r"\b\d{6,}\b"), "<number>"),
]


def normalize_message(message: str | None) -> str:
    if not message:
        return ""
    text = message.strip()
    for pattern, placeholder in _NOISE:
        text = pattern.sub(placeholder, text)
    return " ".join(text.split())


def top_frames(stack_trace: str | None, count: int = 3) -> str:
    """The first `count` frame-looking lines. A stack's tail is framework
    plumbing shared by unrelated errors; its head is what distinguishes them."""
    if not stack_trace:
        return ""
    frames = [
        line.strip()
        for line in stack_trace.splitlines()
        if line.strip().startswith(("at ", "File ", "  File", "\tat "))
        or re.match(r"^\s*\w+\.\w+", line)
    ]
    if not frames:
        frames = [line.strip() for line in stack_trace.splitlines() if line.strip()]
    return "\n".join(normalize_message(f) for f in frames[:count])


def compute_fingerprint(
    error_type: str | None, message: str | None, stack_trace: str | None
) -> str:
    basis = "\n".join(
        [(error_type or "").strip(), normalize_message(message), top_frames(stack_trace)]
    )
    return hashlib.sha256(basis.encode()).hexdigest()


def _check_rate(deployment_id: str) -> None:
    now = time.monotonic()
    bucket = _rate_buckets[deployment_id]
    while bucket and now - bucket[0] > _RATE_WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= RATE_LIMIT_PER_MINUTE:
        raise RateLimited()
    bucket.append(now)


def reset_rate_limits() -> None:
    """Test seam — the buckets are process-global by design."""
    _rate_buckets.clear()


def authenticate_deployment(
    settings: Settings, deployment_id: str, key: str
) -> dict[str, Any] | None:
    """The deployment this key may report against, or None.

    None covers every failure the caller must not be able to tell apart:
    a malformed id, an unknown deployment, reporting switched off, and a
    wrong key. The router answers one generic 401 for all four, so this
    endpoint cannot be used to discover which deployment ids exist.
    """
    if not key or not deployment_id or not _valid_uuid(deployment_id):
        return None
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    with _connect(settings) as conn:
        row = conn.execute(
            """
            select id, org_id, project_id, issue_report_key_hash
            from public.deployments
            where id = %s and issue_reporting_enabled = true
            """,
            (deployment_id,),
        ).fetchone()
    if not row or not row["issue_report_key_hash"]:
        return None
    # Constant-time: a timing side channel here would hand out the hash one
    # byte at a time, which is the whole credential.
    if not hmac.compare_digest(str(row["issue_report_key_hash"]), key_hash):
        return None
    return row


def _self_deployment(settings: Settings) -> dict[str, Any] | None:
    """The deployment Build Mill files its *own* errors against (US-16.8).

    Absent or unflagged configuration disables self-reporting silently: a
    developer running locally should get no warnings on every request and no
    failed writes, just nothing.
    """
    deployment_id = getattr(settings, "self_report_deployment_id", "")
    if not deployment_id or not _valid_uuid(deployment_id):
        return None
    with _connect(settings) as conn:
        return conn.execute(
            """
            select id, org_id, project_id
            from public.deployments
            where id = %s and is_self_monitoring = true
              and issue_reporting_enabled = true
            """,
            (deployment_id,),
        ).fetchone()


# Anything whose *name* suggests a credential is dropped before a report is
# stored, because `context` ends up in a table managers can read and in a
# prompt somebody will paste into an LLM. Substring matching, not equality:
# the header is `authorization`, the field is `github_token`, the env var is
# `SUPABASE_SERVICE_ROLE_KEY`, and none of them are worth enumerating exactly.
_SECRET_HINTS = (
    "auth",
    "cookie",
    "token",
    "key",
    "secret",
    "password",
    "credential",
    "session",
    "signature",
    "bearer",
)


def scrub(value: Any, _depth: int = 0) -> Any:
    """Recursively drop anything credential-shaped from a context bag."""
    if _depth > 6:
        return "<nested too deep>"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key).lower()
            if any(hint in name for hint in _SECRET_HINTS):
                out[str(key)] = "<redacted>"
            else:
                out[str(key)] = scrub(item, _depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [scrub(item, _depth + 1) for item in value]
    return value


# Reporting the failure of reporting is how self-instrumentation takes an app
# down. Module-global rather than per-call: the guard has to survive the stack
# unwinding through the handler that is doing the reporting.
_reporting = False


def self_report(
    settings: Settings,
    error: BaseException,
    context: dict[str, Any] | None = None,
) -> str | None:
    """File one of Build Mill's own errors. Never raises, never blocks.

    Writes through `ingest_report` directly rather than POSTing to our own
    public endpoint: the network hop would add a failure mode to the error
    path, and the endpoint's rate limit is meant for strangers.
    """
    global _reporting
    if _reporting:
        return None
    _reporting = True
    try:
        deployment = _self_deployment(settings)
        if not deployment:
            return None
        result = ingest_report(
            settings,
            deployment,
            {
                "source": "automated",
                "error_type": type(error).__name__,
                "message": str(error),
                "stack_trace": "".join(
                    traceback.format_exception(type(error), error, error.__traceback__)
                ),
                "context": scrub(context or {}),
            },
        )
        return result["id"]
    except Exception:  # noqa: BLE001
        # A self-report that fails is a lost report, never a broken request.
        logger.debug("self-report failed", exc_info=True)
        return None
    finally:
        _reporting = False


def ingest_report(
    settings: Settings, deployment: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    """Record one report against an already-authenticated deployment.

    Returns `{"id": ..., "deduped": bool}`. `org_id`/`project_id` come from the
    deployment row and are never read from the payload — a valid key for one
    deployment must not be able to write against another org's project.
    """
    _check_rate(str(deployment["id"]))

    source = payload.get("source") or "automated"
    if source not in ("automated", "user_report"):
        source = "automated"
    context = _truncate_context(payload.get("context"))
    message = _truncate(payload.get("message"), MESSAGE_LIMIT)
    stack_trace = _truncate(payload.get("stack_trace"), STACK_LIMIT)

    if source == "user_report":
        title = _truncate(payload.get("title"), TITLE_LIMIT) or "Untitled report"
        with _connect(settings) as conn:
            row = conn.execute(
                """
                insert into public.app_issues
                  (org_id, project_id, deployment_id, source, title, message,
                   context, reporter_name, reporter_email)
                values (%s, %s, %s, 'user_report', %s, %s, %s, %s, %s)
                returning id
                """,
                (
                    deployment["org_id"],
                    deployment["project_id"],
                    deployment["id"],
                    title,
                    message,
                    json.dumps(context),
                    _truncate(payload.get("reporter_name"), REPORTER_LIMIT),
                    _truncate(payload.get("reporter_email"), REPORTER_LIMIT),
                ),
            ).fetchone()
            conn.commit()
        return {"id": str(row["id"]), "deduped": False}

    error_type = _truncate(payload.get("error_type"), TITLE_LIMIT) or "Error"
    title = _truncate(payload.get("title"), TITLE_LIMIT) or _truncate(
        f"{error_type}: {payload.get('message') or ''}".strip().rstrip(":"), TITLE_LIMIT
    ) or error_type
    fingerprint = compute_fingerprint(error_type, payload.get("message"), payload.get("stack_trace"))

    # US-79.1 (prod BUG-1): the setup wiring check is a deliberate test ping —
    # proving the pipe is its entire job, so it lands already resolved. Left
    # to land as 'new' it sat in the inbox styled like a crash and was
    # promoted into a bug alongside seven real ones. Structural marker, not
    # message text: the sender stamps `context.component = "verification"`.
    status = "ignored" if context.get("component") == "verification" else "new"

    with _connect(settings) as conn:
        # The partial unique index is what makes the repeat safe under
        # concurrency; ON CONFLICT infers it from the matching predicate.
        # (An 'ignored' verification row sits outside the index predicate, so
        # repeat wiring checks insert fresh rows — each visible, none open.)
        row = conn.execute(
            """
            insert into public.app_issues
              (org_id, project_id, deployment_id, source, fingerprint, title,
               message, stack_trace, context, status)
            values (%s, %s, %s, 'automated', %s, %s, %s, %s, %s, %s)
            on conflict (deployment_id, fingerprint)
              where fingerprint is not null and status in ('new', 'triaged')
            do update set
              occurrence_count = public.app_issues.occurrence_count + 1,
              last_seen_at = now()
            returning id, (xmax <> 0) as deduped
            """,
            (
                deployment["org_id"],
                deployment["project_id"],
                deployment["id"],
                fingerprint,
                title,
                message,
                stack_trace,
                json.dumps(context),
                status,
            ),
        ).fetchone()
        conn.commit()
    return {"id": str(row["id"]), "deduped": bool(row["deduped"])}


# ---------------------------------------------------------------------------
# us-116.8: the fleet-dark System issue.
# ---------------------------------------------------------------------------


def report_fleet_dark(
    settings: Settings,
    org_id: str,
    org_name: str,
    message: str,
    context: dict[str, Any] | None = None,
) -> str | None:
    """File ONE System issue for a fleet-wide outage in an org, against the
    self-monitoring deployment (the same inbox the crash reports use). The
    message carries the outage's start time, so each episode is its own row
    rather than an occurrence count on the last one. None when self-reporting
    is not configured — the manager notification still goes."""
    deployment = _self_deployment(settings)
    if not deployment:
        return None
    result = ingest_report(
        settings,
        deployment,
        {
            "source": "automated",
            "error_type": "FleetDark",
            "title": f"FleetDark: {org_name} — every agent offline",
            "message": message,
            "context": scrub({"org_id": org_id, "org_name": org_name, **(context or {})}),
        },
    )
    return str(result["id"])


def note_returned(settings: Settings, issue_id: str, at: Any) -> None:
    """The fleet came back: record when, on the same issue, and never a second
    notification."""
    if not _valid_uuid(issue_id):
        return
    with _connect(settings) as conn:
        conn.execute(
            """
            update public.app_issues
            set context = coalesce(context, '{}'::jsonb)
                          || jsonb_build_object('returned_at', %s::text)
            where id = %s
            """,
            (at.isoformat() if hasattr(at, "isoformat") else str(at), issue_id),
        )
        conn.commit()
