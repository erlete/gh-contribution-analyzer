---
name: cs-dashboard-ui
description: Rules for building or changing dashboard pages, components, forms and tables. Use for any change under src/gca/web/templates or src/gca/web/static/css.
---

# Dashboard UI

Carbon g90, dark only. Component styles live in
`src/gca/web/static/css/app.css` inside `@layer components`; reusable
markup lives in `src/gca/web/templates/_components.html` as Jinja macros
(kpi, tag, status_tag, insight, text_field, select_field, checkbox, table,
chart, list_builder...). Extend the macro library instead of duplicating
markup in pages.

## Form controls: custom only

Native-looking controls are a defect. Checkbox/radio are drawn with
`appearance: none` and border/background states; selects are wrapped in
`.select` with a mask-image chevron; text inputs use the Carbon field
style (bg `--bg-field-01`, bottom hairline, no border radius). Every new
input type must be styled the same way before it ships — including
`type="search"` and `type="date"` (both bit us once).

## Height discipline

- Fields are `--field-height` (2.5rem); default buttons are `--size-md`
  (2.5rem). An input with a button beside it must use a DEFAULT button,
  never `.small`. The "Replace token" row on Orgs is the reference.
- `.small` buttons (2rem) belong inside table rows and compact strips
  only. The rangebar is a compact strip: its inputs are pinned to
  `--size-sm` so they match its links and Go button.

## General rules (user-mandated, apply everywhere)

- Action affordances are BUTTONS (`.btn` classes on anchors when they
  navigate), never bare links: "Manage recipients", banner "Review" and
  "Open settings" are the precedents. Bare links belong only inside
  prose sentences and data-table entity cells.
- Info icons (`c.info`) carry a -2px nudge in `.info-tip` so the glyph
  centers on the adjacent text line in every context (measured across
  h2, th, labels). Never compensate per-usage.
- Dates display dd/mm/yyyy everywhere. Native date inputs are BANNED in
  visible UI (they render the browser locale): use `c.date_field` /
  `c.date_input`, a masked dd/mm/yyyy text input whose hidden input
  submits ISO; its calendar button opens the off-screen native picker
  via showPicker().
- Entity pickers (people, repos, anything with more than a handful of
  options) use `c.combobox` / `c.combobox_field`: type to filter, pick
  from the list, free text never submits (`.cb-value` only ever holds a
  listed value; empty named comboboxes block submit). `list_builder`
  already embeds one. Native `select` stays for short enums only
  (operation, metric, cadence, report kind).
- Periodic schedule checkboxes default to CHECKED when no saved row
  exists; `ensure_default_schedules` seeds enabled rows at app startup.

## Table action columns

Give the `<td>` holding row buttons `class="cell-actions"`: it right-aligns
the group, forbids wrapping and gives every button an equal `min-width` so
controls line up across rows regardless of label ("Pause" vs "Activate").
Icon-only buttons use `class="icon"` (square, fits inside a tag pill;
inline SVG with `stroke="currentColor"`, plus `aria-label`).

## Layout gotchas

- Any grid cell that can contain a table or long text needs
  `min-width: 0` (`.grid2 > *` already has it) or it blows the layout on
  narrow viewports. Verify at 1440 and 390 wide: zero horizontal overflow.
- Insights load through htmx partials (`/partials/insight?view=...`) with
  a skeleton placeholder; every page always shows an insight (AI or
  deterministic fallback), never an empty slot.
- The selected period persists in a cookie; links only need
  `?range={{ period.key }}` and navigation keeps the window.

See `references/table-rules.md` and `references/copy-rules.md`.
