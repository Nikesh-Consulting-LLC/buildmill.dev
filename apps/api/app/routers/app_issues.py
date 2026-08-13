"""The front door a deployed app reports through (US-16.2).

Public by necessity: the caller is somebody else's running application, not a
Build Mill session, so there is no JWT to check. Authentication is the
deployment's own report key in `X-Report-Key`, the same shape the git remote
uses for worker tokens.

Two rules shape everything here. **One generic 401** — a wrong key, an unknown
deployment id, a malformed one and reporting-switched-off are indistinguishable
from outside, so nobody can walk this endpoint to discover which deployments
exist. And **a minimal response** — `{id, status}` and nothing else, because
every byte this returns is a byte a stranger has learned about the factory.
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Path
from pydantic import BaseModel, Field

from .. import app_issues
from ..config import Settings, get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/report", tags=["app-issues"])

_UNAUTHORIZED = "invalid report key"


class ReportIn(BaseModel):
    """One shape for both sources; `source` picks the branch. Everything is
    optional and everything is capped — the sender is untrusted, and a report
    that arrives half-formed is still worth more than a 422 nobody reads."""

    source: str = Field(default="automated")
    # automated
    error_type: str | None = None
    stack_trace: str | None = None
    severity: str | None = None
    # user_report
    title: str | None = None
    reporter_name: str | None = None
    reporter_email: str | None = None
    # both
    message: str | None = None
    context: Any = None


@router.post("/{deployment_id}/issues", status_code=201)
def submit_report(
    body: ReportIn,
    deployment_id: str = Path(...),
    x_report_key: str = Header(default=""),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    deployment = app_issues.authenticate_deployment(settings, deployment_id, x_report_key)
    if not deployment:
        raise HTTPException(status_code=401, detail=_UNAUTHORIZED)

    try:
        result = app_issues.ingest_report(settings, deployment, body.model_dump())
    except app_issues.RateLimited:
        raise HTTPException(status_code=429, detail="too many reports")
    except Exception:  # noqa: BLE001
        # The reporter is a stranger's app: it learns that something failed,
        # never what. The detail goes to our logs instead.
        logger.exception("app issue ingestion failed for deployment %s", deployment_id)
        raise HTTPException(status_code=500, detail="could not record the report")

    return {"id": result["id"], "status": "accepted"}
