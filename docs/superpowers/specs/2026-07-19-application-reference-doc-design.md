# Design: single-file application reference for agents

**Date:** 2026-07-19
**Status:** Design approved, spec under review

## Problem

There is no one file an agent can read to understand what Software Factory *does*.
The knowledge is spread across `ARCHITECTURE.md` (component boundaries and trust
split), `README.md` (phased roadmap), `CLAUDE.md` (repo conventions), and ~60
completed story files. An agent that needs to operate the application — or to
change it without breaking a surface it never knew existed — has to reconstruct
the picture from all four.

## Audience

Both kinds of agent, one file, sectioned so each reads only what it needs:

- **Operating agents** — an external worker driving the factory through the worker
  MCP, the API, or the gate flow. Needs entry points, auth, lifecycles, permissions.
- **Developing agents** — a coding agent working in this repo. Needs the map of
  surfaces and responsibilities so it changes the right thing in the right place.

## Scope

**In scope:** interfaces and interactions — what each surface exposes, who may call
it, what it changes, and how objects move through their lifecycles.

**Out of scope (explicit):**

- Internals — algorithms, file layout within a component, class structure.
- Request/response schemas — these live in the OpenAPI schema and the generated
  Supabase types; duplicating them here guarantees drift.
- The roadmap and phase history — `README.md` and `stories/completed/` own that.
- Rationale for architectural choices — `ARCHITECTURE.md` owns that. This doc
  links to it rather than restating it.

## Format decision

A **layered reference**: a fixed section ladder, dense tables wherever a table can
carry the meaning, prose only where it cannot. Random access is the priority — an
agent should be able to jump to one section, read it, and stop.

Two alternatives were considered and rejected:

- *Contract-first* (tables only, no prose) — most token-efficient, but carries the
  buttons without the model. Viable only because `ARCHITECTURE.md` exists; still
  too thin for a file that claims to explain the whole application.
- *Scenario-driven* (organized around the end-to-end flows) — reads well, but an
  agent looking up a single endpoint has to hunt for it, and any interface used in
  three flows gets described three times.

The chosen format folds the scenario approach in as **one** section, at the end:
the layers give random access, and a single worked end-to-end scenario proves the
layers connect. Parts list plus assembly instructions.

## Structure

| # | Section | Form | Purpose |
|---|---|---|---|
| 1 | What this is / mental model | Prose, ~30 lines | The one-paragraph answer, plus the human-in-the-loop premise |
| 2 | Actors & surfaces | Table | Every way in: who calls it, with what credential |
| 3 | Domain objects | Table + relationships | project → epic → issue → run → PR → deployment |
| 4 | Lifecycles | State tables | from → to · trigger · who can · side effect |
| 5 | Interface catalog | One table per surface | interface · caller · auth · does · changes |
| 6 | End-to-end scenario | Numbered walkthrough | One story from definition to deployment |
| 7 | Rules & invariants | List | Things that are always true; violating them is a bug |

### Section 2 — actors & surfaces

The four surfaces are genuinely different in caller and credential, and conflating
them is the most likely agent error:

| Surface | Caller | Credential |
|---|---|---|
| Web UI routes | Human manager in a browser | Supabase Auth session (JWT), RLS-scoped |
| FastAPI orchestration | Web app, on the user's behalf | Bearer JWT verified via JWKS |
| Worker MCP | External agent / IDE worker | `X-Worker-Token` |
| Runner WebSocket | Supervisor runner process | Server-issued runner credential |

### Section 5 — interface catalog

One table per surface, columns: interface · who calls it · auth · what it does ·
what it changes. **No request/response schemas** — see out-of-scope above.

### Section 4 — lifecycles

State tables for: issue status, run outcome, gate result, deployment state. This is
the highest-value section for an operating agent and the one most likely to be
wrong if inferred from code, so it is written from the migrations and the routers,
not from memory.

## Maintenance

Hand-written and curated, like `ARCHITECTURE.md`. No generator, no drift check —
accepted cost: the interface catalog is the section that rots first, and it is
updated deliberately when a surface changes.

## Location

`APPLICATION.md` at the repo root, alongside `ARCHITECTURE.md` and `README.md`.
Not `AGENTS.md` — that filename conventionally means *instructions to* a coding
agent, which is `CLAUDE.md`'s job here, not *description of* the application.

`ARCHITECTURE.md`, `README.md`, and `CLAUDE.md` each gain a one-line pointer to it.

## Open question

`CLAUDE.md` states no change lands without a story. This is a documentation file,
not a feature or behavior change, and `stories/` currently holds only the two
Standing QA checklists. Confirm whether this needs a story before implementation.
