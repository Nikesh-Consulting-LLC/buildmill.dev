_Part of the [application reference](../../APPLICATION.md) — the index, audience guide, and rules & invariants live there. Keep this file current in the same commit as the change it describes._

## Actors & surfaces

These are the distinct ways into the system — distinguished by who calls them and what
transport they use, not by a unique credential per row. Worker MCP, the Runner WebSocket, and
the Git proxy all authenticate with the same worker-registry token (`X-Worker-Token`), resolved
through the same lookup server-side, so an agent must not assume a different credential exists
for each surface. The most common agent error here is either conflating two surfaces that
happen to share that token, or wrongly assuming a token valid on one row won't work on another.

| Surface | Who calls it | Credential | Entry point |
|---|---|---|---|
| Web UI | Human manager in a browser | Supabase Auth session (JWT), RLS-scoped | `apps/web/src/app/(app)/` |
| Supabase (direct) | Web app, for plain CRUD | Same browser session, enforced by RLS | Supabase JS SDK |
| FastAPI orchestration | Web app, on the manager's behalf | Bearer JWT, verified server-side via JWKS | `apps/api/app/routers/` |
| Worker MCP | External agent or IDE worker (an autonomous runner or a person's own tool) | Worker registry token, `X-Worker-Token` header or `Authorization: Bearer` (us-115.1) | `apps/api/app/routers/worker.py`, `apps/api/app/factory_mcp.py` |
| Runner WebSocket | The supervisor runner process (`apps/runner/supervisor`) | The *same* worker registry token, sent in the WS handshake (`X-Worker-Token`, with a `params.token` fallback) | `apps/api/app/routers/runner_socket.py` |
| Git proxy | A worker's git client pushing/pulling through the factory remote (optionally an elevated Power Git principal) | The *same* worker registry token again, sent as the HTTP Basic password | `apps/api/app/routers/gitproxy.py` |
| LLM Gateway | A coding-agent CLI module the runner launches, via its provider SDK pointed at the gateway | A short-lived, scoped gateway key minted per run/route over the Runner WebSocket | `apps/api/app/routers/llm_gateway.py` |

**Web UI** — For the manager's own hands-on-keyboard work: defining projects and issues,
reviewing diffs, approving or rejecting, configuring settings. It must never be treated as a
worker or runner identity — it holds a human session, not a service credential.

**Supabase (direct)** — For plain CRUD the web app can do itself under RLS, without a round
trip through `api`. It must never be used for orchestration actions (dispatch, approve, merge)
that `api` needs to own as a side-effecting step.

**FastAPI orchestration** — For actions that genuinely need a server: dispatching work, GitHub
operations, provider routing, and the SSH/SFTP bridge to registered deployment servers. It must
never grow into plain-CRUD territory Supabase/RLS already covers, and it must never accept a
worker token in place of a user's JWT.

**Worker MCP** — Reached over MCP or plain HTTP, for an external coding agent or IDE tool to
pull work from the org's pool, fetch context, and hand back a plan, PRD, or code. It must never
be given GitHub credentials directly — the factory opens PRs itself — and a worker token must
never be treated as identifying a human user.

**Runner WebSocket** — For the supervisor runner's own persistent control channel: presence,
server-pushed config, the LLM inference relay, and command audit. It must never be confused
with a browser-facing socket, and it is not how work is pulled — that still happens over the
HTTP pool.

**Git proxy** — For raw git traffic (clone/fetch/push) between a worker's git client and
GitHub, policy-checked before anything reaches GitHub. It must never be used to bypass the
claimed-run or Power Git rail checks, and the org's real GitHub credential must never leave
this process.

**LLM Gateway** — For a coding-agent CLI module to call its provider's API without ever holding
the org's real provider key. It must never be handed a long-lived credential — the scoped key
is minted per run/route and expires — and it is not the same path as `llm.infer`, which serves
the supervisor's own reasoning, not a CLI module's calls.

