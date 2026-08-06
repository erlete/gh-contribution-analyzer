# Dataviz rules

## Chart type selection

- Time series (daily activity): `line`, dual axis when mixing counts and
  scores (`axes=["Commits", "Significance"]`, second series `axis=1`).
- Composition over time (added vs removed lines): stacked `bar` with a
  `stack` group, optional overlay line on axis 1.
- Rankings and comparisons across people/repos: `hbar` (horizontal bars),
  ordered by value, labels truncated server-side at 22 chars.
- Diverging performance (gains vs losses): single `hbar` series with
  `item_colors`, gainers first (descending), then decliners so the most
  negative sits at the bottom. Consistent bar count: top N + bottom N.

## Palettes (generated, Carbon dataviz)

- Categorical: `palettes.categorical_for(count)` returns the palette sized
  for the series count (1, 2, 3, 4, 5 or 14 groups). Use series order as
  given; do not shuffle colors.
- Sequential ramp: `SEQUENTIAL_BLUE` for single-metric intensity.
- Diverging ramp: `DIVERGENT`, with `DIVERGENT[12]` for positive/growth
  and `DIVERGENT[4]` for negative/decline.
- Axis/grid/tick colors are theme concerns; they come from the generated
  ECharts theme and mplstyle, never from spec code.

## Grammar

- Bars for discrete comparisons, lines for trends; never both meanings in
  one series kind.
- No pie charts.
- Do not invent second axes to force series onto one chart when their
  units differ wildly; split into two charts instead.
- Every chart states its window: include the period label in the
  `description`.
