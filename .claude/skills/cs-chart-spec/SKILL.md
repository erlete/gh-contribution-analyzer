---
name: cs-chart-spec
description: How to add or change charts. Use for anything involving ECharts on the dashboard, matplotlib SVGs in PDFs, chart endpoints or palettes.
---

# ChartSpec

Charts are NEVER built by hand-writing ECharts options or matplotlib calls
in routers or templates. One spec, two renderers:

- `gca.charts.ChartSpec` + `Series` describe the chart
  (kind `line|bar|hbar`, labels, series with `axis`, `stack`,
  `item_colors`, plus `axes`, `description`, `zoom`).
- `render_echarts(spec) -> dict` feeds the web (`/api/charts/*` returns it
  as JSON).
- `render_matplotlib(spec) -> str` returns an SVG string for PDFs.

The web page embeds `{{ c.chart(id, "/api/charts/...?range=" ~ period.key) }}`;
`charts.js` finds `[data-chart][data-src]`, fetches the option JSON with
`cache: 'no-store'` (browsers cache the fetch otherwise) and renders with
the registered `carbon-g90` theme. A ResizeObserver and an
`htmx:afterSwap` hook keep charts alive; no per-page JS.

## Hard-won rules

- Palettes come from generated `gca/charts/palettes.py`:
  `categorical_for(len(series))` picks the Carbon group-count palette.
  Never hardcode colors; diverging charts use `DIVERGENT[12]` (up/blue)
  and `DIVERGENT[4]` (down/red) with per-bar `item_colors`.
- Color is never the only channel: multi-series line charts get the
  dash-pattern cycle as the second channel (the renderers do this).
  EXCEPTION by product decision: Research charts set `palette="wide"`,
  which walks the full 14-color Carbon sequence with solid lines so
  they never look like the dashboard's 2-3 color + dash charts. Both
  renderers honor the flag; use "wide" for every research ChartSpec.
- A y-axis name that survives suppression renders in the strip above
  the plot where the legend lives; `render_echarts` adds a row of grid
  headroom per occupant so they never overlap. Do not work around
  collisions by blanking axis names.
- ECharts 6 category `axisLabel` width/truncate is unreliable: truncate
  long labels SERVER-SIDE in the spec (22 chars + ellipsis).
- Axis names equal to a series name are suppressed by `render_echarts`
  to avoid legend/axis collisions; name axes meaningfully anyway.
- `description` is mandatory: it is the accessible summary of the chart.
- Enable `zoom` when the series can exceed ~60 points.
- Period semantics: range "all" has no previous window. Comparison charts
  must branch to a totals ranking for all-time instead of computing
  deltas against an epoch-shifted window.

See `references/dataviz-rules.md` for chart-type selection and palette
details.
