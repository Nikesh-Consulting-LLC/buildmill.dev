"""US-89.2: the environment is defined once.

One place per project answering "what does an agent get when it works
here?". Rows live in `project_env` (migration 252, plain values inline);
secret VALUES follow the us-1.28 server-credential pattern — written
browser → api → the private `data` Storage bucket, readable by the
service role only, surfaced to humans as `Set · <fingerprint>` and never
read back by a browser.

Delivery: `effective_env` resolves the (project, agent) pair — project-wide
entries plus that agent's scoped ones, scoped wins on a name collision —
and pulls secret values from the bucket. The worker context bundle carries
the result as `environment`; the runner turns it into real process env at
CLI spawn (US-89.1's rule: never a file in the workspace).
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

import psycopg
from psycopg.rows import dict_row

from . import storage
from .config import Settings

logger = logging.getLogger("uvicorn.error")


def _connect(settings: Settings):
    return psycopg.connect(settings.database_url, row_factory=dict_row)


def secret_path(org_id: str, project_id: str, entry_id: str) -> str:
    return f"{org_id}/projects/{project_id}/env/{entry_id}"


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def get_entry(settings: Settings, entry_id: str) -> dict[str, Any] | None:
    with _connect(settings) as conn:
        return conn.execute(
            "select * from public.project_env where id = %s", (entry_id,)
        ).fetchone()


def list_entries(
    settings: Settings, project_id: str, agent_id: str | None = None
) -> list[dict[str, Any]]:
    """Every entry that applies to (project, agent): project-wide rows plus
    the agent's own. With agent_id None, everything the project defines."""
    with _connect(settings) as conn:
        if agent_id:
            return conn.execute(
                "select * from public.project_env"
                " where project_id = %s and (agent_id is null or agent_id = %s)"
                " order by name, agent_id nulls first",
                (project_id, agent_id),
            ).fetchall()
        return conn.execute(
            "select * from public.project_env where project_id = %s"
            " order by name, agent_id nulls first",
            (project_id,),
        ).fetchall()


def mark_secret_set(
    settings: Settings, entry_id: str, fp: str, by: str | None
) -> None:
    with _connect(settings) as conn:
        conn.execute(
            "update public.project_env set fingerprint = %s, updated_by = %s"
            " where id = %s and kind = 'secret'",
            (fp, by, entry_id),
        )
        conn.commit()


def delete_entry(settings: Settings, entry_id: str) -> None:
    with _connect(settings) as conn:
        conn.execute("delete from public.project_env where id = %s", (entry_id,))
        conn.commit()


async def effective_env(
    settings: Settings, project_id: str, agent_id: str | None
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """(values, catalog) for a run/session on (project, agent).

    `values` is what becomes process env — plain values inline, secret
    values read from the bucket (an unset secret is skipped, not empty:
    an empty variable that looks set is worse than an absent one).
    `catalog` is the discovery answer: name/kind/scope/description plus
    whether a secret currently holds a value.
    """
    rows = list_entries(settings, project_id, agent_id)
    # Scoped rows win: sort put project-wide (agent_id null) first, so a
    # later scoped row overwrites it in the dicts below.
    values: dict[str, str] = {}
    catalog: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = row["name"]
        entry: dict[str, Any] = {
            "name": name,
            "kind": row["kind"],
            "description": row["description"] or "",
            "scope": "agent" if row["agent_id"] else "project",
            "set": True,
        }
        if row["kind"] == "plain":
            values[name] = row["value"] or ""
        else:
            if not row["fingerprint"]:
                entry["set"] = False
                catalog[name] = entry
                values.pop(name, None)
                continue
            blob = await storage.get_object(
                settings,
                secret_path(str(row["org_id"]), str(row["project_id"]), str(row["id"])),
            )
            if blob is None:
                logger.warning(
                    "project_env secret %s has a fingerprint but no object", row["id"]
                )
                entry["set"] = False
                catalog[name] = entry
                values.pop(name, None)
                continue
            values[name] = blob.decode("utf-8", "replace")
        catalog[name] = entry
    return values, list(catalog.values())


def secret_values_for_run(
    values: dict[str, str], catalog: list[dict[str, Any]]
) -> list[str]:
    """The delivered SECRET values — what the changeset sweep refuses in
    file contents (US-89.2 AC5) and the scrubber redacts."""
    secret_names = {c["name"] for c in catalog if c["kind"] == "secret" and c["set"]}
    return [v for k, v in values.items() if k in secret_names and len(v) >= 8]
