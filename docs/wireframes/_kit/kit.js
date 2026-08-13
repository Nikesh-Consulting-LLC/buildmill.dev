/* Build Mill wireframe kit — US-48.1
 *
 * Renders the declaration in a page's
 * <script type="application/wireframe+json"> block into app-shaped DOM.
 *
 * The declaration is the artifact; the pixels are a rendering of it. An agent
 * writing {"component": "table", "columns": [...]} is making a statement about
 * information architecture that a code agent can act on. An agent writing a
 * <div> with a border is making a statement about CSS that nobody can act on.
 *
 * A classic script, deliberately NOT an ES module: browsers refuse to load a
 * module over file:// (module scripts are fetched with CORS, and a file://
 * origin is opaque), which would break the one property this kit is required
 * to have — a wireframe opens from disk. No imports, no dependencies, no
 * network.
 *
 * ---------------------------------------------------------------------------
 * DECLARATION
 *
 * {
 *   "story": "US-4.2",
 *   "title": "A manager can filter the queue",
 *   "screens": [{
 *     "name": "Work items",           // required
 *     "route": "/issues",             // optional, shown in the frame header
 *     "shell": "app" | "bare" | "dialog",
 *     "sidebar": {"brand": "…", "items": [{"label": "…", "active": true}]},
 *     "topbar":  [ node, … ],
 *     "states":  ["populated", "empty", "loading", "error"],
 *     "regions": [ node, … ],         // required
 *     "note": "…"
 *   }]
 * }
 *
 * A node is {"component": "<name>", …props, "children": [node, …]}, where
 * <name> is one of the components below — each named for the file it stands
 * for in apps/web/src/components/ui/ or apps/web/src/components/.
 *
 * Every node may carry:
 *   "ac":    2 | [1, 3]   the acceptance criteria this region satisfies
 *   "note":  "…"          a margin annotation
 *   "only":  ["empty"]    render only in these states
 *   "not":   ["loading"]  render in every state but these
 * ------------------------------------------------------------------------ */

const STATES = ['populated', 'empty', 'loading', 'error'];

/* -------------------------------------------------------------------------
 * Tiny DOM helpers
 * ---------------------------------------------------------------------- */

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null && text !== '') node.textContent = String(text);
  return node;
}

function attr(node, name, value) {
  if (value === undefined || value === null || value === '') return node;
  node.setAttribute(name, String(value));
  return node;
}

function append(parent, child) {
  if (child) parent.appendChild(child);
  return parent;
}

function errorNode(message) {
  return el('div', 'wf-error', message);
}

/* A prop that may legitimately arrive as a string, a node, or a list of
 * either. us-42.1's lesson applies to an agent's HTML as much as to its
 * hand-back: coerce what was clearly meant rather than rendering nothing. */
function asList(value) {
  if (value === undefined || value === null) return [];
  return Array.isArray(value) ? value : [value];
}

function isNode(value) {
  return value && typeof value === 'object' && !Array.isArray(value);
}

/* -------------------------------------------------------------------------
 * Components. Each takes (spec, ctx) and returns an Element.
 * ---------------------------------------------------------------------- */

function renderChildren(spec, ctx, into) {
  for (const child of asList(spec.children)) {
    append(into, renderNode(child, ctx));
  }
  return into;
}

function inlineOrText(value, ctx) {
  if (isNode(value)) return renderNode(value, ctx);
  if (value === undefined || value === null || value === '') return null;
  return el('span', 'wf-text', value);
}

const COMPONENTS = {
  /* Layout ------------------------------------------------------------- */

  row(spec, ctx) {
    return renderChildren(spec, ctx, el('div', 'wf-row'));
  },

  stack(spec, ctx) {
    const node = el('div', 'wf-content');
    node.style.padding = '0';
    return renderChildren(spec, ctx, node);
  },

  grid(spec, ctx) {
    const node = el('div', 'wf-grid');
    node.style.setProperty('--wf-cols', String(spec.columns || 2));
    return renderChildren(spec, ctx, node);
  },

  'page-header'(spec, ctx) {
    const node = el('div', 'wf-pageheader');
    const text = el('div', 'wf-pageheader__text');
    append(text, el('div', 'wf-pageheader__title', spec.title));
    if (spec.description) {
      append(text, el('div', 'wf-pageheader__description', spec.description));
    }
    append(node, text);
    const actions = asList(spec.actions);
    if (actions.length) {
      const bar = el('div', 'wf-pageheader__actions');
      for (const action of actions) append(bar, renderNode(action, ctx));
      append(node, bar);
    }
    return node;
  },

  /* card.tsx ----------------------------------------------------------- */

  card(spec, ctx) {
    const node = el('div', 'wf-card');
    if (spec.title || spec.description || spec.action) {
      const header = el('div', 'wf-card__header');
      const text = el('div');
      if (spec.title) append(text, el('div', 'wf-card__title', spec.title));
      if (spec.description) {
        append(text, el('div', 'wf-card__description', spec.description));
      }
      append(header, text);
      if (spec.action) {
        const slot = el('div', 'wf-card__action');
        append(slot, renderNode(spec.action, ctx));
        append(header, slot);
      }
      append(node, header);
    }
    return renderChildren(spec, ctx, node);
  },

  /* button.tsx --------------------------------------------------------- */

  button(spec) {
    const node = el('span', 'wf-button', spec.label || spec.text || 'Button');
    attr(node, 'data-variant', spec.variant || 'default');
    attr(node, 'data-size', spec.size || 'default');
    if (spec.disabled) attr(node, 'data-disabled', 'true');
    return node;
  },

  /* badge.tsx / status-badge.tsx --------------------------------------- */

  badge(spec) {
    const node = el('span', 'wf-badge', spec.label || spec.text || '');
    return attr(node, 'data-variant', spec.variant || 'secondary');
  },

  'status-badge'(spec) {
    const node = el('span', 'wf-statusbadge', spec.label || spec.status || '');
    return attr(node, 'data-tone', spec.tone || 'neutral');
  },

  /* table.tsx ---------------------------------------------------------- */

  table(spec, ctx) {
    /* A table is the component that most needs the state machine: in
     * `loading` it is skeleton rows, in `empty` it is its own empty state,
     * and the author declared neither. */
    const columns = asList(spec.columns);

    if (ctx.state === 'empty') {
      return COMPONENTS['empty-state'](
        {
          title: spec.empty || 'Nothing here yet',
          description: spec.emptyDescription,
          action: spec.emptyAction,
        },
        ctx,
      );
    }

    const wrap = el('div', 'wf-table__wrap');
    const table = el('table', 'wf-table');
    if (columns.length) {
      const head = el('thead');
      const tr = el('tr');
      for (const column of columns) {
        append(tr, el('th', null, isNode(column) ? column.label : column));
      }
      append(head, tr);
      append(table, head);
    }

    const body = el('tbody');
    const rows =
      ctx.state === 'loading'
        ? Array.from({ length: spec.skeletonRows || 3 }, () =>
            columns.map(() => ({ component: 'skeleton' })),
          )
        : asList(spec.rows);

    for (const row of rows) {
      const tr = el('tr');
      for (const cell of asList(row)) {
        const td = el('td');
        append(td, isNode(cell) ? renderNode(cell, ctx) : document.createTextNode(String(cell)));
        append(tr, td);
      }
      append(body, tr);
    }
    append(table, body);
    append(wrap, table);
    return wrap;
  },

  /* input.tsx / textarea.tsx / select.tsx / label.tsx ------------------- */

  field(spec, ctx) {
    const node = el('div', 'wf-field');
    if (spec.label) append(node, el('label', 'wf-label', spec.label));
    const control = spec.control || 'input';
    if (control === 'textarea') {
      append(node, COMPONENTS.textarea(spec, ctx));
    } else if (control === 'select') {
      append(node, COMPONENTS.select(spec, ctx));
    } else if (control === 'checkbox') {
      append(node, COMPONENTS.checkbox(spec, ctx));
    } else {
      append(node, COMPONENTS.input(spec, ctx));
    }
    if (spec.hint) append(node, el('div', 'wf-hint', spec.hint));
    return node;
  },

  input(spec) {
    const filled = spec.value !== undefined && spec.value !== null && spec.value !== '';
    const node = el('div', 'wf-input', filled ? spec.value : spec.placeholder || '');
    if (!filled) attr(node, 'data-placeholder', 'true');
    return node;
  },

  textarea(spec) {
    const filled = spec.value !== undefined && spec.value !== null && spec.value !== '';
    const node = el('div', 'wf-textarea', filled ? spec.value : spec.placeholder || '');
    if (!filled) attr(node, 'data-placeholder', 'true');
    return node;
  },

  select(spec) {
    const chosen = spec.value || spec.placeholder || asList(spec.options)[0] || 'Select…';
    const node = el('div', 'wf-select', chosen);
    if (!spec.value) attr(node, 'data-placeholder', 'true');
    return node;
  },

  checkbox(spec) {
    const node = el('span', 'wf-checkbox', spec.label || '');
    return attr(node, 'data-checked', spec.checked ? 'true' : 'false');
  },

  /* tabs.tsx ----------------------------------------------------------- */

  tabs(spec) {
    const node = el('div', 'wf-tabs');
    const items = asList(spec.items);
    const active = spec.active || (isNode(items[0]) ? items[0].label : items[0]);
    for (const item of items) {
      const label = isNode(item) ? item.label : item;
      const tab = el('div', 'wf-tab', label);
      if (label === active) attr(tab, 'data-active', 'true');
      append(node, tab);
    }
    return node;
  },

  /* dialog.tsx / confirm-dialog.tsx ------------------------------------ */

  dialog(spec, ctx) {
    const scrim = el('div', 'wf-dialog__scrim');
    const node = el('div', 'wf-dialog');
    if (spec.title) append(node, el('div', 'wf-dialog__title', spec.title));
    if (spec.description) {
      append(node, el('div', 'wf-dialog__description', spec.description));
    }
    renderChildren(spec, ctx, node);
    const footer = asList(spec.footer);
    if (footer.length) {
      const bar = el('div', 'wf-dialog__footer');
      for (const action of footer) append(bar, renderNode(action, ctx));
      append(node, bar);
    }
    append(scrim, node);
    return scrim;
  },

  /* empty-state.tsx ---------------------------------------------------- */

  'empty-state'(spec, ctx) {
    const node = el('div', 'wf-empty');
    append(node, el('div', 'wf-empty__title', spec.title || 'Nothing here yet'));
    if (spec.description) append(node, el('div', null, spec.description));
    if (spec.action) {
      const slot = el('div', 'wf-empty__action');
      append(slot, renderNode(spec.action, ctx));
      append(node, slot);
    }
    return node;
  },

  /* toast.tsx ---------------------------------------------------------- */

  toast(spec) {
    const node = el('div', 'wf-toast');
    attr(node, 'data-tone', spec.tone || 'default');
    const text = el('div');
    if (spec.title) append(text, el('div', 'wf-toast__title', spec.title));
    if (spec.description) {
      append(text, el('div', 'wf-toast__description', spec.description));
    }
    return append(node, text);
  },

  /* avatar.tsx / separator.tsx / skeleton.tsx / dropdown-menu.tsx ------- */

  avatar(spec) {
    return el('span', 'wf-avatar', spec.initials || spec.label || '');
  },

  separator(spec) {
    const node = el('div', 'wf-separator');
    return attr(node, 'data-orientation', spec.orientation);
  },

  skeleton(spec) {
    const lines = spec.lines || 1;
    if (lines === 1) {
      const node = el('span', 'wf-skeleton');
      if (spec.width) node.style.width = spec.width;
      return node;
    }
    const wrap = el('div');
    wrap.style.display = 'flex';
    wrap.style.flexDirection = 'column';
    wrap.style.gap = '8px';
    for (let i = 0; i < lines; i += 1) {
      const line = el('span', 'wf-skeleton');
      if (i === lines - 1) line.style.width = '60%';
      append(wrap, line);
    }
    return wrap;
  },

  menu(spec) {
    const node = el('div', 'wf-menu');
    for (const item of asList(spec.items)) {
      const label = isNode(item) ? item.label : item;
      const row = el('div', 'wf-menu__item', label);
      if (isNode(item) && item.active) attr(row, 'data-active', 'true');
      append(node, row);
    }
    return node;
  },

  /* Prose -------------------------------------------------------------- */

  text(spec) {
    const node = el('div', 'wf-text', spec.text || spec.label || '');
    if (spec.muted) attr(node, 'data-muted', 'true');
    return node;
  },

  /* The one component with no counterpart in components/ui: the generated
   * index is a navigation surface, and an index you cannot click is a list.
   * Relative hrefs only — a wireframe reaches nothing off its own folder. */
  link(spec) {
    const href = String(spec.href || '');
    if (/^[a-z]+:/i.test(href) || href.startsWith('//')) {
      return errorNode(`link href must be relative, got "${href}"`);
    }
    const node = el('a', 'wf-link', spec.label || spec.text || href);
    return attr(node, 'href', href);
  },
};

/* Aliases — the names an agent is most likely to reach for by mistake, mapped
 * rather than rejected. A wireframe that fails to render because the author
 * wrote "heading" instead of "page-header" teaches nothing. */
const ALIASES = {
  header: 'page-header',
  pageheader: 'page-header',
  heading: 'page-header',
  emptystate: 'empty-state',
  empty: 'empty-state',
  statusbadge: 'status-badge',
  status: 'status-badge',
  columns: 'grid',
  group: 'row',
  list: 'stack',
  section: 'stack',
  textarea_field: 'field',
  label: 'text',
  paragraph: 'text',
  modal: 'dialog',
  dropdown: 'menu',
  tab: 'tabs',
};

function resolve(name) {
  const key = String(name || '').trim().toLowerCase();
  if (COMPONENTS[key]) return key;
  const alias = ALIASES[key] || ALIASES[key.replace(/[\s_-]/g, '')];
  return alias && COMPONENTS[alias] ? alias : null;
}

/* -------------------------------------------------------------------------
 * Node rendering — state filtering and annotations wrap every component
 * ---------------------------------------------------------------------- */

function visibleIn(spec, state) {
  const only = asList(spec.only);
  if (only.length && !only.includes(state)) return false;
  const not = asList(spec.not);
  if (not.length && not.includes(state)) return false;
  return true;
}

function renderNode(spec, ctx) {
  if (spec === undefined || spec === null) return null;
  if (typeof spec === 'string') return el('div', 'wf-text', spec);
  if (Array.isArray(spec)) {
    const wrap = el('div', 'wf-row');
    for (const item of spec) append(wrap, renderNode(item, ctx));
    return wrap;
  }
  if (!visibleIn(spec, ctx.state)) return null;

  const name = resolve(spec.component || spec.type);
  if (!name) {
    return errorNode(
      `unknown component "${spec.component || spec.type || '(missing)'}" — ` +
        `known: ${Object.keys(COMPONENTS).sort().join(', ')}`,
    );
  }

  let node;
  try {
    node = COMPONENTS[name](spec, ctx);
  } catch (err) {
    return errorNode(`"${name}" failed to render: ${err && err.message}`);
  }
  if (!node) return null;

  const acs = asList(spec.ac);
  if (acs.length || spec.note) {
    const wrap = el('div');
    node.classList.add('wf-annotated');
    append(wrap, node);
    const note = el('div', 'wf-note');
    if (acs.length) {
      append(note, el('span', 'wf-note__ac', `AC ${acs.join(', ')}`));
    }
    if (spec.note) append(note, document.createTextNode(spec.note));
    append(wrap, note);
    return wrap;
  }
  return node;
}

/* -------------------------------------------------------------------------
 * Screens
 * ---------------------------------------------------------------------- */

function renderShell(screen, ctx) {
  const shell = screen.shell || 'app';
  if (shell === 'bare' || shell === 'dialog') {
    const bare = el('div', 'wf-shell wf-shell--bare');
    const main = el('div', 'wf-main');
    const content = el('div', 'wf-content');
    for (const region of asList(screen.regions)) {
      append(content, renderNode(region, ctx));
    }
    append(main, content);
    return append(bare, main);
  }

  const node = el('div', 'wf-shell');
  const sidebar = el('nav', 'wf-sidebar');
  const nav = screen.sidebar || {};
  append(sidebar, el('div', 'wf-sidebar__brand', nav.brand || 'App'));
  for (const item of asList(nav.items)) {
    const label = isNode(item) ? item.label : item;
    const row = el('div', 'wf-navitem', label);
    if (isNode(item) && item.active) attr(row, 'data-active', 'true');
    append(sidebar, row);
  }
  append(node, sidebar);

  const main = el('div', 'wf-main');
  const topbar = asList(screen.topbar);
  if (topbar.length) {
    const bar = el('div', 'wf-topbar');
    for (const region of topbar) append(bar, renderNode(region, ctx));
    append(main, bar);
  }
  const content = el('div', 'wf-content');
  for (const region of asList(screen.regions)) {
    append(content, renderNode(region, ctx));
  }
  append(main, content);
  return append(node, main);
}

function renderScreen(screen, state, showState) {
  const ctx = { state };
  const node = el('section', 'wf-screen');
  const head = el('div', 'wf-screen__head');
  append(head, el('div', 'wf-screen__name', screen.name || 'Screen'));
  if (screen.route) append(head, el('div', 'wf-screen__route', screen.route));
  if (showState) append(head, el('div', 'wf-screen__state', state));
  append(node, head);

  const frame = el('div', 'wf-screen__frame');
  append(frame, renderShell(screen, ctx));
  append(node, frame);

  if (screen.note) append(node, el('div', 'wf-screen__note', screen.note));
  return node;
}

/* -------------------------------------------------------------------------
 * The page's own chrome
 * ---------------------------------------------------------------------- */

function toggleButton(label, pressed, onToggle) {
  const button = el('button', null, label);
  attr(button, 'type', 'button');
  attr(button, 'aria-pressed', pressed ? 'true' : 'false');
  button.addEventListener('click', () => {
    const next = button.getAttribute('aria-pressed') !== 'true';
    button.setAttribute('aria-pressed', next ? 'true' : 'false');
    onToggle(next);
  });
  return button;
}

function renderBar(doc) {
  const bar = el('div', 'wf-bar');
  if (doc.story) append(bar, el('span', 'wf-bar__id', doc.story));
  if (doc.title) append(bar, el('span', 'wf-bar__title', doc.title));
  append(bar, el('span', null, '· wireframe'));

  const spacer = el('span', 'wf-bar__spacer');
  append(bar, spacer);

  append(
    bar,
    toggleButton('Annotations', true, (on) => {
      document.body.classList.toggle('wf-clean', !on);
    }),
  );
  append(
    bar,
    toggleButton('Dark', document.documentElement.classList.contains('dark'), (on) => {
      document.documentElement.classList.toggle('dark', on);
    }),
  );
  return bar;
}

/* -------------------------------------------------------------------------
 * Entry point
 * ---------------------------------------------------------------------- */

function render(doc, mount) {
  const root = mount || document.body;
  root.innerHTML = '';
  const page = el('div', 'wf-page');
  append(page, renderBar(doc));

  const screens = el('div', 'wf-screens');
  const declared = asList(doc.screens);

  if (!declared.length) {
    append(
      screens,
      errorNode('this wireframe declares no screens — "screens" is missing or empty'),
    );
  }

  for (const screen of declared) {
    const states = asList(screen.states).filter((s) => STATES.includes(s));
    const list = states.length ? states : ['populated'];
    for (const state of list) {
      append(screens, renderScreen(screen, state, list.length > 1));
    }
  }

  append(page, screens);
  append(root, page);
  return page;
}

function parse(text) {
  return JSON.parse(text);
}

function boot() {
  const source = document.querySelector('script[type="application/wireframe+json"]');
  if (!source) {
    document.body.appendChild(
      errorNode(
        'no declaration found — this page needs a ' +
          '<script type="application/wireframe+json"> block',
      ),
    );
    return;
  }
  let doc;
  try {
    doc = parse(source.textContent);
  } catch (err) {
    document.body.appendChild(
      errorNode(`the declaration is not valid JSON: ${err && err.message}`),
    );
    return;
  }
  render(doc);
}

/* Exposed for a host that wants to render a declaration itself — the app's own
 * preview panel renders through this rather than shipping a second renderer. */
window.BuildMillWireframe = { render, parse, COMPONENTS };

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}
