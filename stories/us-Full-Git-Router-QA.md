# Full Git Router QA

**Status:** Standing
**Type:** Living QA checklist — not tied to any phase

## Story

The factory git remote ([us-3.8](completed/phase-03-workers-agent-connectivity.md), URL scheme [us-3.13](completed/phase-03-workers-agent-connectivity.md)) is a smart-HTTP proxy that stands in front of GitHub: workers `git clone`/`fetch`/`push` through `https://<api-host>/git/<org-shortname>/<project-slug>.git` with their worker token, and the factory injects the GitHub credential server-side. Because it is a **custom remote**, the risk is that a plain git client hits an operation the proxy doesn't faithfully pass through — or a push policy it enforces incorrectly.

This is a **living, git-client-driven QA checklist** that exercises the router **independent of the Build Mill app and independent of the MCP `submit_changeset` transport** — nothing but stock command-line `git` pointed at the factory remote. The goal is to prove the repo is **fully git-client compatible**: every read operation a normal client performs passes through and matches what GitHub returns, every write the policy permits lands correctly on GitHub, and every write the policy forbids is refused with a readable git error rather than a corrupt state or a hung connection. Re-run whenever the proxy (`apps/api/app/routers/gitproxy.py`), the auth/registry path, or the push policy changes; grow it as new git surfaces are supported.

## Test environment & method

- **Target:** the live factory api host's git endpoint — the exact remote URL is the **factory git remote URL** shown on the Projects page for the **Demo** project ([us-3.9](completed/phase-03-workers-agent-connectivity.md)), of the form `https://<api-host>/git/<org-shortname>/demo.git`. Upstream is Demo's linked GitHub repo.
- **Client:** stock `git` (command line) on a machine with **no GitHub credentials configured** — the whole point is that only a worker token is needed. No custom tooling, no factory app, no MCP.
- **Auth:** HTTP Basic — username ignored, **password = a live worker token** for Demo's org (from the worker's registration / token reveal, [us-3.20](completed/phase-03-workers-agent-connectivity.md)). Claude does not mint or enter production credentials; the manager supplies a test worker token and, where a push must succeed, a **currently-claimed** work item whose branch the push targets.
- **Method:** run each git operation, capture the client's stdout/stderr (use `GIT_TRACE=1` / `-v` where the refusal message matters), and confirm the GitHub side out-of-band (the branch/commit/tag as it actually appears on github.com, and the push as recorded on the run and activity feed).
- **Recording:** each check gets PASS / FAIL with the observed evidence (client output + the GitHub-side result); failures capture the exact command, expected vs. actual, the git-protocol error text, and any server-side error.

## Test plan

### A. Handshake, auth & addressing
- [ ] **Ref advertisement:** `git ls-remote <factory-url>` (with token) lists the same refs as the upstream GitHub repo — both `git-upload-pack` and `git-receive-pack` service advertisements return over smart-HTTP.
- [ ] **No creds → challenge:** an unauthenticated request (`git ls-remote` with no credentials) is refused **401** carrying the `WWW-Authenticate: Basic` challenge, so git re-prompts / retries with the token — it does not hang or 500.
- [ ] **Bad / revoked token → 401:** a garbage token, and a **revoked** worker's token, are both refused 401 — revoking a worker cuts its git access immediately (no cache reprieve on auth).
- [ ] **Cross-org → 404:** a valid worker token against a project slug **outside that worker's org** (or an unknown org-shortname/slug) → **404 repository not found**, indistinguishable from a non-existent repo (no org enumeration).
- [ ] **Capability-gated fetch → 404:** for a worker **not allow-listed** on Demo ([us-3.12](completed/phase-03-workers-agent-connectivity.md)), the clone/fetch handshake answers **404** exactly like a cross-org repo; an allow-listed worker clones fine.
- [ ] **Broken org credential → 403:** with Demo's GitHub App/PAT disconnected or invalid, a git operation surfaces the **403 credential message** ([us-5.24](completed/phase-05-mcp-maturity-everything-a-coding-agent-needs.md)), not a bare 404 — the client sees the right status to chase.
- [ ] **Readable URL:** the remote reads `/git/<org-shortname>/demo.git` (not a bare uuid); the `.git` suffix is optional and both resolve.

### B. Clone & fetch — read path (must pass through faithfully)
- [ ] **Full clone:** `git clone <factory-url>` produces a working tree whose `HEAD` commit SHA and default branch **exactly match** the upstream GitHub repo.
- [ ] **Shallow clone:** `git clone --depth 1 <factory-url>` succeeds and yields a shallow history.
- [ ] **Single-branch clone:** `git clone --single-branch --branch <b> <factory-url>` fetches only that branch.
- [ ] **Incremental fetch:** after new commits land upstream, `git fetch` / `git pull` retrieves them; `git fetch --all --prune` reconciles deleted upstream branches.
- [ ] **Tags fetched:** existing upstream tags come down on clone / `git fetch --tags` (reading tags works even though pushing them does not — see C).
- [ ] **Protocol v2:** `git -c protocol.version=2 clone <factory-url>` succeeds (protocol negotiation passes through, not just v0).
- [ ] **Streaming / large repo:** cloning a repo with large blobs completes without the proxy buffering whole packfiles (no memory blow-up, no timeout); a gzip-negotiated fetch works and so does a plain one.
- [ ] **Plumbing sanity:** `git remote -v`, `git log`, `git diff`, and a second re-clone into a fresh dir are all consistent — the remote behaves like an ordinary origin.

### C. Push — write path & policy (permitted lands, forbidden refused)
- [ ] **Permitted push lands:** on a **currently-claimed** Demo work item, push the branch the work context names (`factory/<slug>-<id6>`, or legacy `factory/issue-<uuid>`) → succeeds; the branch and commit appear on github.com with **identical SHAs**.
- [ ] **Fast-forward continues:** a second fast-forward push to the same claimed branch succeeds; the run's recorded head SHA advances.
- [ ] **Default-branch write refused:** `git push origin HEAD:main` (or the repo default) is **refused** with a readable `remote error: push rejected: …` — unless the run is a `main`-strategy run whose branch_ref is the default branch ([us-7.3](completed/phase-07-project-setup-release-readiness-factory-intelligence.md)), which is the one case it lands.
- [ ] **Unclaimed / non-convention branch refused:** pushing a branch that matches **no running claim of yours** (e.g. `my-feature`) is refused, naming the fix (claim the item, push the branch the context names).
- [ ] **Other worker's claim refused:** pushing to a branch whose run **another worker** holds is refused.
- [ ] **Force-push / history rewrite refused:** `git push --force` where the old head ≠ the last head the factory recorded is refused as a history rewrite — the branch on GitHub is unchanged.
- [ ] **Branch deletion refused:** `git push origin :<branch>` (zero-SHA update) is refused ("branch deletion is not allowed").
- [ ] **Tag push refused:** `git push --tags` / `git push origin <tag>` is refused — **only branch refs are writable** through the factory remote (matches us-3.8's "only branch refs are writable" policy; tags/notes/other refs are read-only through the proxy).
- [ ] **gzip push body:** a client that gzip-compresses the request body (`http.postBuffer` / large push) is accepted — the proxy gunzips to parse the command section, then streams the packfile through.
- [ ] **Clean refusal, not a hang:** every refusal above prints `remote error: push rejected: …` and the client exits non-zero cleanly — never "the remote end hung up unexpectedly", and never a partially-applied push (all-or-nothing across multiple ref updates in one push).

### D. GitHub-side parity & side effects (no webhooks needed)
- [ ] **Author identity:** a landed commit is authored/attributable to the **worker/principal**, and the push is recorded on the claimed run (head SHA + pushed-at) and lands in the activity feed **naming the worker** — the factory saw the push itself, with no GitHub webhook configured ([us-3.8](completed/phase-03-workers-agent-connectivity.md) → [us-3.4](completed/phase-03-workers-agent-connectivity.md)).
- [ ] **Reconciliation / PR:** the pushed branch is picked up by PR reconciliation ([us-3.4](completed/phase-03-workers-agent-connectivity.md)) — a `pr`-mode run opens/updates a PR, a `direct`/main-strategy run lands with no PR — matching `submit_mode`, entirely from the router-observed push.
- [ ] **No credential leak:** the GitHub installation token never appears in the git client's verbose/trace output, in any error body, or in server logs; the client only ever authenticates with its own worker token.
- [ ] **Token cache cutoff:** after the ~50-min server-side token cache window, operations keep working via transparent refresh; and revoking the GitHub credential upstream is the hard cutoff (documents the known cache-TTL reprieve from `gitproxy.py`).

## Acceptance criteria

- [ ] Every check is executed with a **plain git client against the live factory remote** — no Build Mill UI, no MCP transport — and recorded PASS/FAIL with evidence (client output plus the GitHub-side result).
- [ ] All **read** operations (Section B) pass through faithfully and match what the upstream GitHub repo returns; all **permitted writes** (Section C) land on GitHub with identical SHAs; all **forbidden writes** are refused with a readable git-protocol error and leave GitHub unchanged.
- [ ] No operation corrupts state, hangs the client, leaks the GitHub credential, or bypasses the org/capability/claim boundaries.
- [ ] Failures are logged with the exact command, expected vs. actual, the git error text, and any server-side error, and triaged into fixes on `gitproxy.py` (or the story, if the policy is what's wrong).

## Out of scope

- **Git LFS** — a separate batch protocol, declared unsupported until a project needs it ([us-3.8](completed/phase-03-workers-agent-connectivity.md) out-of-scope); confirming it is *refused/unsupported* is in scope, exercising it is not.
- **SSH transport** — HTTPS only; there is no SSH endpoint to test.
- The **MCP `submit_changeset` / `get_workspace`** git-free transport — that is a separate code hand-back path with its own coverage, not this git-client test.
- The **Build Mill web app** and read-only code browsing in the UI — covered by [Full App Browser QA](us-Full-App-Browser-QA.md).
- Load / performance / security fuzzing beyond the streaming and leak sanity checks above — this is functional git-client compatibility, not a pen test.

## Features covered

- The factory git remote and its addressing/policy — [us-3.8](completed/phase-03-workers-agent-connectivity.md) (smart-HTTP proxy + push policy), [us-3.13](completed/phase-03-workers-agent-connectivity.md) (org/slug URL scheme), [us-3.9](completed/phase-03-workers-agent-connectivity.md) (git URLs surfaced), [us-3.1](completed/phase-03-workers-agent-connectivity.md) / [us-3.20](completed/phase-03-workers-agent-connectivity.md) (worker-token Basic auth), [us-3.12](completed/phase-03-workers-agent-connectivity.md) (capability-gated fetch), [us-5.24](completed/phase-05-mcp-maturity-everything-a-coding-agent-needs.md) (actionable credential errors), and the GitHub-side effects in [us-3.4](completed/phase-03-workers-agent-connectivity.md) — this checklist verifies them all through a real git client.
