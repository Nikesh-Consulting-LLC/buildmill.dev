# Wireframes

This folder is written by Build Mill. It holds one HTML wireframe per work
item, drawn by an agent before the item was planned.

## What is here

```
docs/wireframes/README.md        this file
docs/wireframes/index.html       every wireframe, grouped by feature
docs/wireframes/us-4.2.html      one story's wireframe
docs/wireframes/_kit/kit.css     the styling every wireframe renders through
docs/wireframes/_kit/kit.js      the renderer
docs/wireframes/_kit/tokens.css  this project's design tokens
```

Open any `.html` file directly — no server, no build step, no network. Each
page carries two toggles: **Annotations** (the acceptance criteria each region
satisfies) and **Dark**.

## Who owns it

**Build Mill owns this whole folder, and regenerates it wholesale.** Anything
here that a regeneration no longer produces is deleted, so everything you find
here is current. A file added by hand does not survive.

The source of truth is the wireframe artifact stored on the work item in the
app, not the file. The file is a copy, written so the repository — and the
agent that codes the story — can read it without asking the app.

Paths are work-item **ids**, never titles, so retitling a story moves nothing.

## What a wireframe is, and is not

It is a statement about layout, hierarchy, states and copy: what is on the
screen, in what order, and what it looks like when it is loading, empty,
broken, or full.

It is **not** a mockup, a prototype, or a promise about pixels. It has no
imagery and no interaction beyond the two toggles. Where the built UI has to
depart from the wireframe, the departure belongs in the coding agent's
hand-back notes, not in a silent redraw.

## How a page is written

Each page is a small JSON declaration in a
`<script type="application/wireframe+json">` block, rendered by `_kit/kit.js`
into components named for the app's own — `card`, `table`, `status-badge`,
`empty-state`, `tabs`, `dialog`, `field`, `button`. Reading a wireframe tells
you which component to reach for; the names are meant to be greppable in the
codebase.

`_kit/kit.js` documents the full declaration format at the top of the file.
