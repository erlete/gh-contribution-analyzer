/* Small vanilla-JS helpers shared by every page. No framework, no build. */

/* Combobox: filter-then-select entity picker. The visible input only
   filters; the hidden .cb-value input carries the chosen option's value,
   so free text can never reach the server. Blurring with text that does
   not match a choice reverts to the last selection (or clears). */
function initComboboxes(root) {
  (root || document).querySelectorAll('[data-combobox]').forEach(function (box) {
    if (box.dataset.ready) return;
    box.dataset.ready = '1';
    var input = box.querySelector('.cb-input');
    var hidden = box.querySelector('.cb-value');
    var list = box.querySelector('.cb-list');
    var items = Array.prototype.slice.call(list.querySelectorAll('li'));
    var active = -1;

    function visibleItems() {
      return items.filter(function (li) {
        return !li.hidden && !li.dataset.taken;
      });
    }

    function open() {
      list.hidden = false;
      input.setAttribute('aria-expanded', 'true');
    }

    function close() {
      list.hidden = true;
      input.setAttribute('aria-expanded', 'false');
      setActive(-1);
    }

    function setActive(index) {
      var vis = visibleItems();
      items.forEach(function (li) { li.classList.remove('active'); });
      active = index;
      if (index >= 0 && vis[index]) {
        vis[index].classList.add('active');
        vis[index].scrollIntoView({ block: 'nearest' });
      }
    }

    function filter() {
      var q = input.value.trim().toLowerCase();
      items.forEach(function (li) {
        li.hidden = q !== '' && li.textContent.toLowerCase().indexOf(q) === -1;
      });
      setActive(-1);
    }

    function choose(li) {
      hidden.value = li.dataset.value;
      input.value = li.textContent.trim();
      box.dataset.label = input.value;
      box.classList.remove('missing');
      close();
      hidden.dispatchEvent(new Event('change', { bubbles: true }));
    }

    function revert() {
      if (box.dataset.label && hidden.value) {
        input.value = box.dataset.label;
      } else {
        input.value = '';
        hidden.value = '';
      }
    }

    var seeded = items.filter(function (li) { return li.dataset.selected; })[0];
    if (seeded) { hidden.value = seeded.dataset.value; input.value = seeded.textContent.trim(); box.dataset.label = input.value; }

    input.addEventListener('focus', function () { filter(); open(); });
    input.addEventListener('input', function () { filter(); open(); });
    input.addEventListener('keydown', function (e) {
      var vis = visibleItems();
      if (e.key === 'ArrowDown') { e.preventDefault(); if (list.hidden) open(); setActive(Math.min(active + 1, vis.length - 1)); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(Math.max(active - 1, 0)); }
      else if (e.key === 'Enter') {
        if (!list.hidden && vis.length) { e.preventDefault(); choose(vis[active >= 0 ? active : 0]); }
      }
      else if (e.key === 'Escape') { close(); revert(); }
    });
    list.addEventListener('pointerdown', function (e) {
      var li = e.target.closest('li');
      if (li && !li.dataset.taken) { e.preventDefault(); choose(li); }
    });
    input.addEventListener('blur', function () {
      setTimeout(function () {
        if (box.contains(document.activeElement)) return;
        close();
        if (input.value.trim() !== (box.dataset.label || '')) revert();
      }, 120);
    });
  });
}

/* A named combobox with nothing chosen would submit an empty value the
   server rejects with a bare 422; block the submit and flag the field
   instead. Unnamed comboboxes (list builders) are exempt. */
document.addEventListener('submit', function (e) {
  var missing = false;
  e.target.querySelectorAll('[data-combobox] .cb-value[name]').forEach(function (h) {
    if (!h.value) {
      missing = true;
      h.closest('[data-combobox]').classList.add('missing');
    }
  });
  if (missing) e.preventDefault();
});

/* List builder: a combobox of candidates plus an "Add" button feeding a
   scrollable list of chosen entries, one per line, each removable. Chosen
   values submit as hidden inputs named after data-field. Initial entries
   come from the data-values JSON attribute: [{value, label}, ...]. */
function initListBuilders(root) {
  (root || document).querySelectorAll('.list-builder').forEach(function (builder) {
    if (builder.dataset.ready) return;
    builder.dataset.ready = '1';
    var field = builder.dataset.field;
    var box = builder.querySelector('[data-combobox]');
    var cbInput = box.querySelector('.cb-input');
    var cbHidden = box.querySelector('.cb-value');
    var options = Array.prototype.slice.call(box.querySelectorAll('.cb-list li'));
    var addBtn = builder.querySelector('.lb-add > button');
    var list = builder.querySelector('.lb-items');
    var empty = builder.querySelector('.lb-empty');

    function chosenValues() {
      return Array.prototype.map.call(
        list.querySelectorAll('input'),
        function (i) { return i.value; }
      );
    }

    function sync() {
      var chosen = chosenValues();
      options.forEach(function (li) {
        if (chosen.indexOf(li.dataset.value) !== -1) li.dataset.taken = '1';
        else delete li.dataset.taken;
      });
      if (empty) empty.style.display = list.children.length ? 'none' : '';
    }

    function add(value, label) {
      if (!value || chosenValues().indexOf(value) !== -1) return;
      var li = document.createElement('li');
      var span = document.createElement('span');
      span.textContent = label;
      var input = document.createElement('input');
      input.type = 'hidden';
      input.name = field;
      input.value = value;
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'lb-remove';
      btn.textContent = 'remove';
      btn.setAttribute('aria-label', 'Remove ' + label);
      btn.addEventListener('click', function () { li.remove(); sync(); });
      li.appendChild(span);
      li.appendChild(input);
      li.appendChild(btn);
      list.appendChild(li);
      sync();
    }

    function addCurrent() {
      var value = cbHidden.value;
      var label = cbInput.value.trim();
      if (!value) return;
      add(value, label);
      cbHidden.value = '';
      cbInput.value = '';
      delete box.dataset.label;
    }

    addBtn.addEventListener('click', addCurrent);
    cbHidden.addEventListener('change', addCurrent);

    var seed = [];
    try { seed = JSON.parse(builder.dataset.values || '[]'); } catch (e) { seed = []; }
    seed.forEach(function (entry) {
      if (typeof entry === 'string') add(entry, entry);
      else add(String(entry.value), entry.label);
    });
    sync();
  });
}

/* Date fields: the visible input masks itself to dd/mm/yyyy while typing;
   only a complete, real date lands in the hidden ISO input (anything
   partial clears it, and the server treats empty as unset). The calendar
   button forwards to a hidden native date input via showPicker(). */
function initDateFields(root) {
  (root || document).querySelectorAll('[data-datefield]').forEach(function (field) {
    if (field.dataset.ready) return;
    field.dataset.ready = '1';
    var text = field.querySelector('.date-text');
    var hidden = field.querySelector('input[type="hidden"]');
    var native = field.querySelector('.date-native');
    var calBtn = field.querySelector('.date-cal');

    function toDisplay(iso) {
      var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || '');
      return m ? m[3] + '/' + m[2] + '/' + m[1] : '';
    }

    function commit() {
      var m = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(text.value);
      if (m) {
        var day = +m[1], month = +m[2], year = +m[3];
        var probe = new Date(Date.UTC(year, month - 1, day));
        if (probe.getUTCFullYear() === year && probe.getUTCMonth() === month - 1 &&
            probe.getUTCDate() === day) {
          hidden.value = year + '-' + m[2] + '-' + m[1];
          native.value = hidden.value;
          text.classList.remove('invalid');
          return;
        }
      }
      hidden.value = '';
      native.value = '';
      text.classList.toggle('invalid', text.value.trim() !== '');
    }

    text.addEventListener('input', function () {
      var digits = text.value.replace(/\D/g, '').slice(0, 8);
      var out = digits.slice(0, 2);
      if (digits.length > 2) out += '/' + digits.slice(2, 4);
      if (digits.length > 4) out += '/' + digits.slice(4);
      text.value = out;
      commit();
    });
    text.addEventListener('blur', commit);

    calBtn.addEventListener('click', function () {
      if (native.showPicker) {
        try { native.showPicker(); } catch (e) { native.click(); }
      } else {
        native.click();
      }
    });
    native.addEventListener('change', function () {
      text.value = toDisplay(native.value);
      commit();
    });

    var initial = hidden.value || text.dataset.iso || '';
    if (initial) { text.value = toDisplay(initial); commit(); }
  });
}

/* Row filter: an <input data-filter-rows="#table-id"> hides rows whose text
   does not contain the query. Header rows (no <td>) always stay visible. */
function initRowFilters(root) {
  (root || document).querySelectorAll('input[data-filter-rows]').forEach(function (input) {
    if (input.dataset.ready) return;
    input.dataset.ready = '1';
    var target = document.querySelector(input.dataset.filterRows);
    if (!target) return;
    input.addEventListener('input', function () {
      var q = input.value.trim().toLowerCase();
      target.querySelectorAll('tr').forEach(function (row) {
        if (!row.querySelector('td')) return;
        row.style.display =
          !q || row.textContent.toLowerCase().indexOf(q) !== -1 ? '' : 'none';
      });
    });
  });
}

/* Auto-submit: a [data-autosubmit] wrapper submits its form when the select
   inside it changes. The submit button stays as the no-JS fallback. */
function initAutosubmit(root) {
  (root || document).querySelectorAll('[data-autosubmit] select').forEach(function (select) {
    if (select.dataset.ready) return;
    select.dataset.ready = '1';
    select.addEventListener('change', function () {
      if (select.form) select.form.submit();
    });
  });
}

/* Info tooltips: .info-tip tips are position: fixed so scroll containers
   cannot clip them; place and clamp each tip when its trigger is hovered
   or focused. Escape blurs the trigger, which hides the tip. */
function placeInfoTip(e) {
  var wrap = e.target && e.target.closest ? e.target.closest('.info-tip') : null;
  if (!wrap) return;
  var tip = wrap.querySelector('.tip');
  if (!tip) return;
  var r = wrap.getBoundingClientRect();
  var pad = 8;
  var half = tip.offsetWidth / 2;
  var x = r.left + r.width / 2;
  x = Math.max(pad + half, Math.min(x, window.innerWidth - pad - half));
  var y = r.bottom + 6;
  if (y + tip.offsetHeight > window.innerHeight - pad) {
    y = r.top - tip.offsetHeight - 6;
  }
  tip.style.setProperty('--tip-x', x + 'px');
  tip.style.setProperty('--tip-y', y + 'px');
}
document.addEventListener('pointerover', placeInfoTip);
document.addEventListener('focusin', placeInfoTip);
document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape' && document.activeElement &&
      document.activeElement.closest && document.activeElement.closest('.info-tip')) {
    document.activeElement.blur();
  }
});

/* Research composer: the operation select decides which entity and metric
   slots are visible, and the info icon next to it mirrors the selected
   operation's explanation. Ops config rides in the form's data-ops JSON:
   {op: {slots: [...], info: "..."}}. */
function initResearchComposer(root) {
  var form = (root || document).querySelector('#block-composer');
  if (!form || form.dataset.ready) return;
  form.dataset.ready = '1';
  var ops = {};
  try { ops = JSON.parse(form.dataset.ops || '{}'); } catch (e) { ops = {}; }
  var opSelect = form.querySelector('select[name="op"]');
  var entitySelect = form.querySelector('select[name="entity"]');
  var infoBtn = form.querySelector('.op-info .info-btn');
  var infoTip = form.querySelector('.op-info .tip');
  if (!opSelect) return;

  function apply() {
    var def = ops[opSelect.value] || { slots: [], info: '' };
    var slots = def.slots.slice();
    // The entity chip swaps which hand-picked list shows; scope-wide ops
    // (spotlight) carry "entity" without any list to swap.
    if (slots.indexOf('entity') !== -1 &&
        (def.slots.indexOf('people') !== -1 || def.slots.indexOf('repos') !== -1)) {
      var wanted = entitySelect && entitySelect.value === 'repos' ? 'repos' : 'people';
      slots = slots.filter(function (s) { return s !== 'people' && s !== 'repos'; });
      slots.push(wanted);
    }
    form.querySelectorAll('[data-slot]').forEach(function (wrap) {
      wrap.hidden = slots.indexOf(wrap.dataset.slot) === -1;
    });
    if (infoBtn) infoBtn.setAttribute('aria-label', def.info);
    if (infoTip) infoTip.textContent = def.info;
  }

  opSelect.addEventListener('change', apply);
  if (entitySelect) entitySelect.addEventListener('change', apply);
  apply();
}

/* Command palette: Ctrl+K (or the topbar button) opens an overlay that
   jumps to any person, repository, research, page or period switch. Items
   load once per page from /api/palette; matching is substring-based with
   prefix and word-start matches ranked first. */
function initPalette() {
  var overlay = document.getElementById('palette');
  if (!overlay) return;
  var input = overlay.querySelector('.palette-input');
  var list = overlay.querySelector('.palette-list');
  var items = null;
  var filtered = [];
  var active = 0;

  function open() {
    overlay.hidden = false;
    input.value = '';
    render('');
    input.focus();
  }

  function close() {
    overlay.hidden = true;
  }

  function load() {
    if (items) return Promise.resolve(items);
    return fetch('/api/palette')
      .then(function (r) { return r.json(); })
      .then(function (d) { items = d.items; return items; });
  }

  function score(label, q) {
    var l = label.toLowerCase();
    if (l.indexOf(q) === 0) return 0;
    var idx = l.indexOf(q);
    if (idx === -1) return -1;
    var prev = l[idx - 1];
    return prev === ' ' || prev === '/' ? 1 : 2;
  }

  function go(item) {
    close();
    location.href = item.url;
  }

  function render(q) {
    load().then(function (all) {
      q = q.trim().toLowerCase();
      var scored = [];
      all.forEach(function (item) {
        if (!q) {
          // Empty query: offer navigation and period switches, not the
          // full entity dump.
          if (item.kind === 'page' || item.kind === 'action') scored.push([0, item]);
          return;
        }
        var s = score(item.label, q);
        if (s >= 0) scored.push([s, item]);
      });
      scored.sort(function (a, b) {
        return a[0] - b[0] || a[1].label.localeCompare(b[1].label);
      });
      filtered = scored.slice(0, 12).map(function (pair) { return pair[1]; });
      active = 0;
      list.innerHTML = '';
      filtered.forEach(function (item, index) {
        var li = document.createElement('li');
        li.setAttribute('role', 'option');
        var label = document.createElement('span');
        label.textContent = item.label;
        var kind = document.createElement('span');
        kind.className = 'palette-kind';
        kind.textContent = item.kind;
        li.appendChild(label);
        li.appendChild(kind);
        if (index === 0) li.classList.add('active');
        li.addEventListener('pointerdown', function (e) {
          e.preventDefault();
          go(item);
        });
        list.appendChild(li);
      });
    });
  }

  function move(delta) {
    var lis = list.children;
    if (!lis.length) return;
    lis[active].classList.remove('active');
    active = (active + delta + lis.length) % lis.length;
    lis[active].classList.add('active');
    lis[active].scrollIntoView({ block: 'nearest' });
  }

  input.addEventListener('input', function () { render(input.value); });
  input.addEventListener('keydown', function (e) {
    if (e.key === 'ArrowDown') { e.preventDefault(); move(1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); move(-1); }
    else if (e.key === 'Enter') { if (filtered[active]) go(filtered[active]); }
    else if (e.key === 'Escape') { close(); }
  });
  overlay.addEventListener('pointerdown', function (e) {
    if (e.target === overlay) close();
  });
  document.addEventListener('keydown', function (e) {
    if ((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K')) {
      e.preventDefault();
      if (overlay.hidden) open(); else close();
    }
  });
  document.querySelectorAll('[data-palette-open]').forEach(function (btn) {
    btn.addEventListener('click', open);
  });
}

/* Drill-down rows: a [data-drill="#row-id"] button toggles the hidden
   sibling row whose content htmx loads on the first click. Delegated so
   it survives htmx swaps. */
document.addEventListener('click', function (e) {
  var btn = e.target && e.target.closest ? e.target.closest('[data-drill]') : null;
  if (!btn) return;
  var row = document.querySelector(btn.dataset.drill);
  if (row) row.hidden = !row.hidden;
});

document.addEventListener('DOMContentLoaded', function () {
  initComboboxes();
  initListBuilders();
  initDateFields();
  initRowFilters();
  initAutosubmit();
  initResearchComposer();
  initPalette();
});
