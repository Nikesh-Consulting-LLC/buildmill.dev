_Part of the [application reference](../../APPLICATION.md) — the index, audience guide, and rules & invariants live there. Keep this file current in the same commit as the change it describes._

## End to end: one story

One story, start to finish — the mainstream path an operating agent will actually meet: an
already-defined story issue, planned, coded, reviewed, merged, and only then deployed as a
separate, deliberate action. PRD drafting, feature breakdown, and the full test-case UI are real
paths through the same tables; showing all of them here would show none clearly, so where they
matter they appear as one-line branch points below instead.

1. **Manager defines the issue.** On `/issues`, the manager writes a story (`type` defaults to
   `story`) with acceptance criteria. A new `issues` row is inserted at `draft`.
2. **Manager dispatches it for planning.** The manager calls `POST /issues/{issue_id}/dispatch`
   (`dispatch_issue`). A `plan`-kind run is created; the issue moves `draft` → `queued`.
3. **Worker discovers the work.** The agent calls `list_available_work` over Worker MCP. The
   queued plan run shows up in the org's pool.
4. **Worker claims it.** The agent calls `claim_work`. The run is held by that worker; the issue
   moves `queued` → `planning`.
5. **Worker gathers context.** The agent calls `get_work_context`, then `get_repo_tree` and
   `read_repo_file` to study the repo before writing anything. All read-only; each call extends
   the claim's lease.
6. **Worker hands back the plan.** The agent calls `submit_plan` with an implementation plan and
   a test plan. The run reaches `succeeded`; the issue moves `planning` → `plan-review`.
7. **Manager approves the plan.** The manager calls `POST /issues/{issue_id}/plan/approve`. The
   test plan's cases materialize into `test_cases`; the issue moves `plan-review` → `planned`.
8. **Manager dispatches the code run — a separate action from approving the plan.** The manager
   calls `POST /issues/{issue_id}/dispatch` again, now against a `planned` issue with an approved
   plan artifact. A `code`-kind run is created; the issue moves `planned` → `queued`.
9. **Worker claims the code run.** The agent calls `claim_work`. The issue moves `queued` →
   `running`.
10. **Worker fetches a working copy.** The agent calls `get_workspace` — the tree as a zip pinned
    to a `base_sha`, no git tooling required.
11. **Worker implements, then dry-runs the hand-back.** After writing the code, the agent calls
    `validate_submission` to catch fixable issues before committing to a submission.
12. **Worker hands back the code.** The agent calls `submit_changeset` with the changed files and
    its `base_sha`. The run reaches `succeeded`; the factory builds the commit, pushes it, and
    opens the PR itself; the issue moves `running` → `in-review`.
13. **Worker records its own verification.** The agent calls `report_test_results` against the
    cases materialized in step 7.
14. **Worker checks the PR side.** The agent calls `get_pr_status` to see CI and mergeability
    while it waits on review.
15. **Manager reviews and approves.** On `/review/[issueId]`, the manager calls
    `POST /runs/{run_id}/approve`. The PR is merged on GitHub; the issue moves `in-review` →
    `merged`.
16. **Manager triggers a deployment — a separate, deliberate action, not a continuation of the
    merge.** On the project's deployment page, the manager calls `POST /deployments/{id}/run`. A
    new `deployment_runs` row is inserted at `queued`.
17. **The pipeline picks the run up.** The deployment run moves `queued` → `running`.
18. **The deployment finishes.** The pipeline completes without error; the deployment run moves
    `running` → `succeeded`, and `deployments.current_run_id` is updated to point at it.

Where the happy path forks:

- **A gate rejects the code.** The manager calls `POST /runs/{run_id}/reject` with a required
  comment; the issue moves `in-review` → `needs-fixes`, and the comment rides along as context
  for whatever the manager dispatches next.
- **The manager sends the plan back instead of approving it.** `POST /issues/{issue_id}/plan/send-back`
  with a required comment; the issue returns to whatever status it had when this plan run was
  dispatched.
- **The worker has pushed the branch to the factory remote.** It calls `submit_code_work` instead
  of `submit_changeset`; the PR opens and the issue advances the same way.
- **The worker can't proceed without an answer.** It calls `request_clarification` instead of
  guessing or releasing; the claim stays held and the lease extends until the manager answers.
- **The worker's assumptions no longer match the run's state.** It calls `release_work`; the run
  returns to `queued` and the issue is forced back to `queued` with it.
- **The run fails outright.** The runner — not an MCP tool — calls
  `POST /worker/runs/{run_id}/submit` with the `Submit` body's `error` field set; the run moves
  to `failed` and the issue moves to `failed` too.

