/* Progressive enhancement for the gallery: search, facet filters, status, sort.
   The page is fully rendered at build time; this only shows/hides/reorders cards. */
(() => {
  'use strict';

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

  const grid = $('#grid');
  if (!grid) return;

  const cells = $$('.cell', grid).map((el) => ({
    el,
    name: el.dataset.name,
    search: el.dataset.search,
    status: el.dataset.status,
    featured: el.dataset.featured === '1',
    updated: el.dataset.updated,
    released: el.dataset.released,
    tags: new Set(el.dataset.tags ? el.dataset.tags.split('|') : []),
    stack: new Set(el.dataset.stack ? el.dataset.stack.split('|') : []),
  }));
  const defaultOrder = cells.map((c) => c.el);

  const ui = {
    q: $('#q'),
    sort: $('#sort'),
    count: $('#count'),
    active: $('#active-filters'),
    clear: $('#clear'),
    empty: $('#empty'),
    status: $('#status'),
    menus: $$('details.menu'),
  };

  // Facets: "tags" and "stack" share the same shape. URL param names are kept short and readable.
  const FACETS = { tags: { param: 'tag', label: 'Tag' }, stack: { param: 'tech', label: 'Tech' } };
  const labels = { tags: new Map(), stack: new Map() };
  for (const menu of ui.menus) {
    for (const opt of $$('.opt', menu)) {
      labels[menu.dataset.facet].set($('input', opt).value, $('.opt-label', opt).textContent);
    }
  }

  const state = { q: '', tags: new Set(), stack: new Set(), status: 'all', sort: 'featured' };

  /* ------------------------------------------------------------ filtering */

  const matches = (c) => {
    if (state.status !== 'all' && c.status !== state.status) return false;
    for (const f of Object.keys(FACETS)) {
      // OR within a facet, AND across facets.
      if (state[f].size && ![...state[f]].some((k) => c[f].has(k))) return false;
    }
    if (state.q) {
      const words = state.q.toLowerCase().split(/\s+/).filter(Boolean);
      if (!words.every((w) => c.search.includes(w))) return false;
    }
    return true;
  };

  const sorters = {
    featured: (a, b) => b.featured - a.featured || b.updated.localeCompare(a.updated) || a.name.localeCompare(b.name),
    updated: (a, b) => b.updated.localeCompare(a.updated) || a.name.localeCompare(b.name),
    released: (a, b) => b.released.localeCompare(a.released) || a.name.localeCompare(b.name),
    name: (a, b) => a.name.localeCompare(b.name),
  };

  const filtersActive = () => state.q || state.status !== 'all' || state.tags.size || state.stack.size;

  function render() {
    // sort (build-time order is the "featured" order, so just restore it for that case)
    const ordered = [...cells].sort(sorters[state.sort] || sorters.featured);
    const frag = document.createDocumentFragment();
    ordered.forEach((c) => frag.append(c.el));
    grid.append(frag);

    let shown = 0;
    for (const c of cells) {
      const ok = matches(c);
      c.el.hidden = !ok;
      if (ok) shown++;
    }

    ui.count.textContent = filtersActive()
      ? `${shown} of ${cells.length} project${cells.length === 1 ? '' : 's'}`
      : `${cells.length} project${cells.length === 1 ? '' : 's'}`;
    ui.empty.hidden = shown > 0 || cells.length === 0;

    renderActiveFilters();
    syncControls();
    writeUrl();
  }

  function renderActiveFilters() {
    ui.active.replaceChildren();
    const add = (kind, key, text, onRemove) => {
      const li = document.createElement('li');
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'pill';
      b.setAttribute('aria-label', `Remove filter ${kind}: ${text}`);
      const small = document.createElement('small');
      small.textContent = kind;
      b.append(small, document.createTextNode(text));
      b.insertAdjacentHTML('beforeend', '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg>');
      b.addEventListener('click', onRemove);
      li.append(b);
      ui.active.append(li);
    };
    for (const [f, meta] of Object.entries(FACETS)) {
      for (const key of state[f]) add(meta.label, key, labels[f].get(key) || key, () => toggle(f, key, false));
    }
    if (state.status !== 'all') {
      const btn = $(`[data-status="${state.status}"]`, ui.status || document);
      add('Status', state.status, btn ? btn.textContent : state.status, () => setStatus('all'));
    }
    ui.active.hidden = ui.active.childElementCount === 0;
    ui.clear.hidden = !filtersActive();
  }

  function syncControls() {
    if (ui.q.value !== state.q) ui.q.value = state.q;
    ui.sort.value = state.sort;
    for (const menu of ui.menus) {
      const f = menu.dataset.facet;
      $$('input[type=checkbox]', menu).forEach((cb) => { cb.checked = state[f].has(cb.value); });
      const badge = $('.badge', menu);
      badge.textContent = state[f].size;
      badge.hidden = state[f].size === 0;
    }
    if (ui.status) $$('button', ui.status).forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.status === state.status)));
    // highlight chips that are currently active
    for (const chip of $$('.chip[data-facet]')) {
      chip.classList.toggle('on', state[chip.dataset.facet].has(chip.dataset.key));
      chip.setAttribute('aria-pressed', String(state[chip.dataset.facet].has(chip.dataset.key)));
    }
  }

  function toggle(facet, key, on) {
    state[facet][on ? 'add' : 'delete'](key);
    render();
  }

  function setStatus(value) {
    state.status = value;
    render();
  }

  function reset() {
    state.q = '';
    state.status = 'all';
    state.tags.clear();
    state.stack.clear();
    render();
  }

  /* ------------------------------------------------------------ URL state */

  function writeUrl() {
    const p = new URLSearchParams();
    if (state.q) p.set('q', state.q);
    for (const [f, meta] of Object.entries(FACETS)) state[f].forEach((k) => p.append(meta.param, k));
    if (state.status !== 'all') p.set('status', state.status);
    if (state.sort !== 'featured') p.set('sort', state.sort);
    const qs = p.toString();
    try {
      history.replaceState(null, '', location.pathname + (qs ? `?${qs}` : '') + location.hash);
    } catch (e) { /* e.g. sandboxed iframe - the gallery still works */ }
  }

  function readUrl() {
    const p = new URLSearchParams(location.search);
    state.q = (p.get('q') || '').trim();
    for (const [f, meta] of Object.entries(FACETS)) {
      p.getAll(meta.param).forEach((k) => { if (labels[f].has(k)) state[f].add(k); });
    }
    const status = p.get('status');
    if (status && (!ui.status || $(`[data-status="${CSS.escape(status)}"]`, ui.status))) state.status = status;
    const sort = p.get('sort');
    if (sort && sorters[sort]) state.sort = sort;
  }

  /* ------------------------------------------------------------ events */

  ui.q.addEventListener('input', () => { state.q = ui.q.value.trim(); render(); });
  ui.q.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && ui.q.value) { ui.q.value = ''; state.q = ''; render(); }
  });
  ui.sort.addEventListener('change', () => { state.sort = ui.sort.value; render(); });
  ui.clear.addEventListener('click', reset);
  $('#reset')?.addEventListener('click', reset);
  ui.status?.addEventListener('click', (e) => {
    const b = e.target.closest('button[data-status]');
    if (b) setStatus(b.dataset.status);
  });

  for (const menu of ui.menus) {
    menu.addEventListener('change', (e) => {
      if (e.target.matches('input[type=checkbox]')) toggle(menu.dataset.facet, e.target.value, e.target.checked);
    });
    const filter = $('.panel-filter', menu);
    filter?.addEventListener('input', () => {
      const needle = filter.value.trim().toLowerCase();
      $$('.opt', menu).forEach((o) => { o.hidden = needle && !o.textContent.toLowerCase().includes(needle); });
    });
    menu.addEventListener('toggle', () => {
      if (menu.open) {
        ui.menus.forEach((m) => { if (m !== menu) m.open = false; });
        (filter || $('input', menu))?.focus({ preventScroll: true });
      }
    });
  }
  document.addEventListener('click', (e) => {
    ui.menus.forEach((m) => { if (m.open && !m.contains(e.target)) m.open = false; });
  });

  // Clicking a chip on a card filters by it; clicking an active chip removes the filter.
  grid.addEventListener('click', (e) => {
    const chip = e.target.closest('.chip[data-facet]');
    if (chip) {
      const { facet, key } = chip.dataset;
      toggle(facet, key, !state[facet].has(key));
    }
  });

  document.addEventListener('keydown', (e) => {
    const typing = e.target.closest?.('input, textarea, select, [contenteditable]');
    if (e.key === '/' && !typing && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      ui.q.focus();
      ui.q.select();
    } else if (e.key === 'Escape') {
      const open = ui.menus.find((m) => m.open);
      if (open) { open.open = false; $('summary', open).focus(); }
    }
  });

  /* ------------------------------------------------------------ card behaviour */

  const overlayMode = matchMedia('(hover: hover) and (min-width: 760px)');

  // On desktop a card grows from the centre of its grid cell (see the "pop out" block in style.css).
  // Near the window edges that could push it under the top bar or off the bottom, so work out how far
  // to nudge it (--shift) to keep the whole card visible. If the card is taller than the window,
  // its top stays visible.
  const MARGIN = 16;
  const placeCell = (cell) => {
    if (!overlayMode.matches) { cell.style.removeProperty('--shift'); return; }
    const grown = cell.offsetHeight + $('.more-inner', cell).scrollHeight + 14;   // collapsed height + details + gap
    const r = cell.getBoundingClientRect();                                       // the cell itself never moves
    const centred = r.top + r.height / 2 - grown / 2;                             // top edge if grown around the centre
    const minTop = Math.max($('.topbar').getBoundingClientRect().bottom, 0) + MARGIN;
    const maxTop = Math.max(innerHeight - grown - MARGIN, minTop);
    const top = Math.min(Math.max(centred, minTop), maxTop);
    cell.style.setProperty('--shift', `${Math.round(top - centred)}px`);
  };
  grid.addEventListener('pointerover', (e) => { const c = e.target.closest('.cell'); if (c) placeCell(c); });
  grid.addEventListener('focusin', (e) => { const c = e.target.closest('.cell'); if (c) placeCell(c); });

  // Keep the raised card inside the window if the page is scrolled or resized while it is open.
  let queued = false;
  const refresh = () => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      const c = grid.querySelector('.cell:hover') || document.activeElement?.closest?.('.cell');
      if (c) placeCell(c);
    });
  };
  addEventListener('scroll', refresh, { passive: true });
  addEventListener('resize', refresh);

  // Touch / narrow screens: tap a card to expand it inline.
  grid.addEventListener('click', (e) => {
    if (overlayMode.matches || e.target.closest('a, button')) return;
    const card = e.target.closest('.card');
    if (card) card.classList.toggle('open');
  });
  grid.addEventListener('keydown', (e) => {
    if ((e.key === 'Enter' || e.key === ' ') && e.target.classList?.contains('card') && !overlayMode.matches) {
      e.preventDefault();
      e.target.classList.toggle('open');
    }
  });

  // Broken or missing images fall back to the gradient + initials placeholder underneath.
  const dropBroken = (img) => { if (img.complete && img.naturalWidth === 0) img.closest('.bg') ? img.closest('.bg').remove() : img.remove(); };
  document.addEventListener('error', (e) => {
    if (e.target instanceof HTMLImageElement) {
      const bg = e.target.closest('.bg');
      (bg || e.target).remove();
    }
  }, true);
  $$('.card img').forEach(dropBroken);

  /* ------------------------------------------------------------ theme */

  $('#theme')?.addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem('gallery-theme', next); } catch (e) { /* private mode */ }
  });

  /* ------------------------------------------------------------ go */

  readUrl();
  render();
})();
