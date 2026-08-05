/* ECharts bootstrap: registers the Carbon g90 theme once, then renders
   every [data-chart][data-src] element from its server-built option.
   Feature code never writes chart options in the browser. */
(function () {
  var themePromise = null;

  function themeReady() {
    if (!themePromise) {
      themePromise = fetch('/static/echarts-theme.json')
        .then(function (r) { return r.json(); })
        .then(function (theme) { echarts.registerTheme('carbon-g90', theme); });
    }
    return themePromise;
  }

  function render(el) {
    if (el.dataset.ready) return;
    el.dataset.ready = '1';
    themeReady()
      .then(function () { return fetch(el.dataset.src, { cache: 'no-store' }); })
      .then(function (r) { return r.json(); })
      .then(function (option) {
        var chart = echarts.init(el, 'carbon-g90');
        chart.setOption(option);
        if (typeof ResizeObserver !== 'undefined') {
          new ResizeObserver(function () { chart.resize(); }).observe(el);
        }
      })
      .catch(function () {
        el.classList.add('empty-state');
        el.textContent = 'Chart failed to load.';
      });
  }

  function initCharts(root) {
    (root || document).querySelectorAll('[data-chart][data-src]').forEach(render);
  }

  document.addEventListener('DOMContentLoaded', function () { initCharts(); });
  document.addEventListener('htmx:afterSwap', function (event) {
    initCharts(event.target);
  });
  window.gcaInitCharts = initCharts;
})();
