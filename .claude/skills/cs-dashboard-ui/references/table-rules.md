# Table rules

Every data table uses the `c.table()` macro (`table.data` class).

- No zebra striping, ever. Row separation is the hairline bottom border;
  hover is `--bg-layer-hover-01`.
- No uppercase headers. Headers are sentence case, semibold, sticky
  (`position: sticky; top: 0`) on `--bg-layer-accent-01`.
- Numeric columns get `class="num"` on both `th` and `td`: right-aligned
  with `font-variant-numeric: tabular-nums`. Units go in
  `<span class="unit">` inside the header, never repeated per cell.
- Format numbers with the `int_fmt` / `float_fmt` / `pct` filters, never
  raw `{{ value }}`.
- Row heights: `--row-short` default, `--row-compact` via `table.data.compact`.
- Every table has a `caption` (visible helper text describing the table).
- Long tables get a `.rowfilter` search input wired with
  `data-filter-rows="#table-id"` for client-side filtering.
- Action columns: `<td class="cell-actions">` wrapping an `.actions` div;
  right-aligned, nowrap, equal-width buttons.
- Empty states use `c.empty_row(colspan, message)`, never a bare empty body.
- A table inside a grid cell requires the cell to have `min-width: 0`.
