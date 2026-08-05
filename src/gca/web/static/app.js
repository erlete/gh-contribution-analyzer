/* Small vanilla-JS helpers shared by every page. No framework, no build. */

/* List builder: a <select> of candidates plus an "Add" button feeding a
   scrollable list of chosen entries, one per line, each removable. Chosen
   values submit as hidden inputs named after data-field. Initial entries
   come from the data-values JSON attribute: [{value, label}, ...]. */
function initListBuilders(root) {
  (root || document).querySelectorAll('.list-builder').forEach(function (builder) {
    if (builder.dataset.ready) return;
    builder.dataset.ready = '1';
    var field = builder.dataset.field;
    var select = builder.querySelector('select');
    var addBtn = builder.querySelector('.lb-add button');
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
      Array.prototype.forEach.call(select.options, function (o) {
        var taken = chosen.indexOf(o.value) !== -1;
        o.disabled = taken;
        o.hidden = taken;
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

    addBtn.addEventListener('click', function () {
      var option = select.selectedOptions[0];
      if (option && !option.disabled) add(option.value, option.textContent.trim());
    });

    var seed = [];
    try { seed = JSON.parse(builder.dataset.values || '[]'); } catch (e) { seed = []; }
    seed.forEach(function (entry) {
      if (typeof entry === 'string') add(entry, entry);
      else add(String(entry.value), entry.label);
    });
    sync();
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

document.addEventListener('DOMContentLoaded', function () {
  initListBuilders();
  initRowFilters();
  initAutosubmit();
});
