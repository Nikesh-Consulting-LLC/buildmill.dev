#!/usr/bin/env python3
"""Seed synthetic Phase-2 demo data for UI testing.

Requires DATABASE_URL (or apps/api/.env) and an existing project + org.
Creates: epic, feature (+PRD), child stories, bug, chore, plan artifacts,
approvals, a fake merged release record.

Usage:
  cd apps/api
  .venv/Scripts/python ../../scripts/seed-phase2-demo.py --project-id <uuid>

Optional: --user-id <auth.users uuid> for approval actor (defaults to first org member).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

import psycopg
from psycopg.rows import dict_row


def load_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    env = Path(__file__).resolve().parents[1] / "apps" / "api" / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip().strip('"')
    sys.exit("DATABASE_URL not set")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--user-id", default=None)
    args = parser.parse_args()

    with psycopg.connect(load_database_url(), row_factory=dict_row) as conn:
        project = conn.execute(
            "select id, org_id, name from public.projects where id = %s",
            (args.project_id,),
        ).fetchone()
        if not project:
            sys.exit(f"project {args.project_id} not found")

        user_id = args.user_id
        if not user_id:
            member = conn.execute(
                "select user_id from public.organization_members where org_id = %s limit 1",
                (project["org_id"],),
            ).fetchone()
            if not member:
                sys.exit("no org member to attribute approvals to")
            user_id = str(member["user_id"])

        org_id = project["org_id"]
        project_id = project["id"]

        epic_id = uuid.uuid4()
        conn.execute(
            """
            insert into public.epics (id, org_id, project_id, title, description, status)
            values (%s, %s, %s, %s, %s, 'open')
            """,
            (
                epic_id,
                org_id,
                project_id,
                "Demo: customer notifications",
                "Synthetic epic for Phase-2 UI walkthrough.",
            ),
        )

        feature_id = uuid.uuid4()
        conn.execute(
            """
            insert into public.issues
              (id, org_id, project_id, type, epic_id, title, body, acceptance_criteria, status)
            values (%s, %s, %s, 'feature', %s, %s, %s, %s::jsonb, 'ready')
            """,
            (
                feature_id,
                org_id,
                project_id,
                epic_id,
                "[sim:ok] Email digest for unread alerts",
                "Operators want a daily digest of unread alerts instead of a noisy stream.",
                json.dumps(["Digest email sends once daily", "User can opt out"]),
            ),
        )

        prd = """## Problem

Operators miss important alerts buried in a noisy stream.

## Goals

- Daily digest of unread alerts
- Opt-out preference

## Out of scope

- SMS / push channels

## Acceptance criteria

- [ ] Digest email sends once daily
- [ ] User can opt out
"""
        conn.execute(
            """
            insert into public.artifacts
              (org_id, issue_id, kind, content, version, status, created_by)
            values (%s, %s, 'prd', %s, 1, 'approved', 'llm')
            """,
            (org_id, feature_id, prd),
        )
        conn.execute(
            """
            insert into public.approvals
              (org_id, issue_id, gate, subject_type, decision, actor, comment)
            values (%s, %s, 'prd', 'artifact', 'approved', %s, 'Looks good for demo')
            """,
            (org_id, feature_id, user_id),
        )

        story_ids = []
        for title, body in [
            (
                "[sim:ok] Digest preference API",
                "As an operator I can set digest preferences via API.",
            ),
            (
                "[sim:ok] Digest email renderer",
                "As an operator I receive a readable HTML digest.",
            ),
        ]:
            sid = uuid.uuid4()
            story_ids.append(sid)
            conn.execute(
                """
                insert into public.issues
                  (id, org_id, project_id, type, parent_id, epic_id, title, body,
                   acceptance_criteria, status)
                values (%s, %s, %s, 'story', %s, %s, %s, %s, %s::jsonb, 'draft')
                """,
                (
                    sid,
                    org_id,
                    project_id,
                    feature_id,
                    epic_id,
                    title,
                    body,
                    json.dumps(["Works in demo"]),
                ),
            )

        bug_id = uuid.uuid4()
        conn.execute(
            """
            insert into public.issues
              (id, org_id, project_id, type, epic_id, title, body, acceptance_criteria, status)
            values (%s, %s, %s, 'bug', %s, %s, %s, %s::jsonb, 'plan-review')
            """,
            (
                bug_id,
                org_id,
                project_id,
                epic_id,
                "[sim:ok] Alert badge count wrong after mark-read",
                "## Repro\n1. Open alerts\n2. Mark one read\n3. Badge still shows old count\n\n"
                "## Expected\nBadge decrements immediately.",
                json.dumps(["Badge matches unread count"]),
            ),
        )
        plan = "## Root cause\n\nStale client cache.\n\n## Implementation plan\n\nInvalidate badge query on mark-read."
        test_plan = (
            "## Test plan\n\n```json\n"
            + json.dumps(
                [
                    {
                        "title": "Badge updates after mark-read",
                        "steps": "1. Mark alert read\n2. Check badge",
                        "expected_result": "Count decrements",
                        "test_types": ["regression"],
                        "environments": ["dev", "uat"],
                    }
                ],
                indent=2,
            )
            + "\n```\n"
        )
        for kind, content in (("plan", plan), ("test_plan", test_plan)):
            conn.execute(
                """
                insert into public.artifacts
                  (org_id, issue_id, kind, content, version, status, created_by)
                values (%s, %s, %s, %s, 1, 'draft', 'agent')
                """,
                (org_id, bug_id, kind, content),
            )

        chore_id = uuid.uuid4()
        conn.execute(
            """
            insert into public.issues
              (id, org_id, project_id, type, title, body, acceptance_criteria, status)
            values (%s, %s, %s, 'chore', %s, %s, '[]'::jsonb, 'draft')
            """,
            (
                chore_id,
                org_id,
                project_id,
                "[sim:ok] Bump alert-client dependency",
                "Short chore: bump @factory/alerts to latest patch.",
            ),
        )

        # Merged story + release record for Releases UI
        merged_id = uuid.uuid4()
        run_id = uuid.uuid4()
        conn.execute(
            """
            insert into public.issues
              (id, org_id, project_id, type, epic_id, title, body, acceptance_criteria, status)
            values (%s, %s, %s, 'story', %s, %s, %s, %s::jsonb, 'merged')
            """,
            (
                merged_id,
                org_id,
                project_id,
                epic_id,
                "Seeded merged story for release timeline",
                "Already merged — used to exercise release records.",
                json.dumps(["Shipped"]),
            ),
        )
        conn.execute(
            """
            insert into public.runs
              (id, org_id, issue_id, provider, status, kind, input_context,
               pr_url, merge_commit_sha, finished_at)
            values (%s, %s, %s, 'claude', 'succeeded', 'code', '{}'::jsonb,
                    'simulated://pr/seed-merged', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', now())
            """,
            (run_id, org_id, merged_id),
        )
        rr_id = uuid.uuid4()
        conn.execute(
            """
            insert into public.release_records
              (id, org_id, issue_id, run_id, merge_commit_sha)
            values (%s, %s, %s, %s, %s)
            """,
            (
                rr_id,
                org_id,
                merged_id,
                run_id,
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            ),
        )
        conn.execute(
            """
            insert into public.release_record_events
              (org_id, release_record_id, environment, kind, actor)
            values (%s, %s, 'dev', 'deployed', %s)
            """,
            (org_id, rr_id, user_id),
        )

        for iid, etype, payload in [
            (feature_id, "created", {"title": "feature"}),
            (feature_id, "prd-approved", {}),
            (feature_id, "stories-created", {"story_ids": [str(s) for s in story_ids]}),
            (bug_id, "plan-ready", {}),
        ]:
            conn.execute(
                """
                insert into public.issue_events (org_id, issue_id, type, payload)
                values (%s, %s, %s, %s::jsonb)
                """,
                (org_id, iid, etype, json.dumps(payload)),
            )

        conn.commit()
        print("Seeded Phase-2 demo data:")
        print(f"  project:  {project['name']} ({project_id})")
        print(f"  epic:     {epic_id}")
        print(f"  feature:  {feature_id} (ready + approved PRD + 2 stories)")
        print(f"  bug:      {bug_id} (plan-review — open Review)")
        print(f"  chore:    {chore_id} (draft — dispatch for plan)")
        print(f"  merged:   {merged_id} (release record with dev deploy)")
        print(f"  release:  {rr_id}")


if __name__ == "__main__":
    main()
