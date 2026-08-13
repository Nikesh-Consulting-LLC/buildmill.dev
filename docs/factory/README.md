# Factory documentation tree

Build Mill (the software factory) writes the project's work into this
folder: one folder per feature, named for the feature's work-item id,
holding its approved PRD (`prd.md`) and one file per story named for the
story's id.

## Finding things

- `index.json` — every item in build order, with its id, parentage,
  whether it has an approved plan, and its path. One read; no parsing of
  prose. This is what an agent should use.
- `INDEX.md` — the same list with titles and links, for humans.
- Every `.md` file opens with YAML front matter carrying the same fields,
  so `grep` over the tree works too.

Paths are **ids, not titles** (`us-4.1/us-4.2.md`). Retitling an item
changes its `H1` and its index entry and moves nothing.

## What appears here, and when

- A **feature** appears once its PRD is approved.
- A **story** appears once it is dispatched — before anyone has planned
  it. `has_plan: false` says so. This is deliberate: an agent needs to see
  the stories around it, not just the one in front of it.
- A story that has shipped gains an `## Outcome` section naming the merge
  commit, the files it touched, and what the agent said on the way out.

**Build Mill owns this tree.** It is a projection of the factory's state,
regenerated wholesale on every write:

- Edits made here by hand are overwritten on the next write and are not
  recovered — change the source of truth in Build Mill instead.
- A file the factory stops generating is deleted, so everything you find
  here is current.
- Mutable state — status, assignees, comments, run history — lives in
  Build Mill only.
- Git history carries the previous versions.
