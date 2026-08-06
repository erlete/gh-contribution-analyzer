---
name: cs-pdf-report
description: How to build or change PDF reports (WeasyPrint, dark g90, Plex fonts, SVG charts). Use for anything under src/gca/reports.
---

# PDF reports

Reports are WeasyPrint documents styled by `styles/tokens-print.css`
(generated) plus `styles/report.css` (hand-written, dark A4 with @page
margin boxes). Templates extend `report_base.html`; shared blocks live in
`_macros.html` (`narrative_box`, `chart_block`). No inline styles in
templates.

## The font rule (non-negotiable)

WeasyPrint `@font-face` silently falls back to DejaVu unless ONE shared
`FontConfiguration` instance is passed to every `CSS(...)` object AND to
`write_pdf(...)`. `builder.render_pdf` already does this; never construct
CSS objects outside it. Verification is mechanical, never visual: open the
generated PDF and list its embedded fonts; only subsetted IBM Plex may
appear. If DejaVu shows up, the FontConfiguration wiring broke.

## Layout constraints (WeasyPrint reality)

- Paginating layouts must be block or table. Grid/flex do not fragment
  across pages; the KPI grid uses inline-block tiles for this reason.
- `var()` and `target-counter()` ARE supported: tokens work in print CSS
  and the TOC gets page numbers via `target-counter(attr(href), page)`.
- Tables: `thead` repeats per page automatically; give `tr`
  `break-inside: avoid`. Sections start on a fresh page with
  `break-before: page`.
- Charts are SVG strings from `render_matplotlib` (ChartSpec). The
  renderer registers Plex via `font_manager.addfont`, sets
  `svg.fonttype: none` (text stays text) and strips the XML prolog so the
  SVG can be inlined. Never embed PNGs.

## Content coherence

- A filtered report (person_ids/repo_ids in params) computes its intro
  narrative and key numbers over the SELECTION and says so
  ("N selected out of M active"); only percentile positions rank against
  the whole scope.
- Period kind "all" means all-time: insight contexts omit every
  previous-period key and narratives must not compare against one.
- AI narratives cap at `AI_SECTION_CAP` sections; beyond it the
  deterministic fallback renders, marked without the AI chip.

## Verification

Generate inside the container (worker image has the fonts), then:
1. list the PDF's embedded fonts (Plex only),
2. rasterize a couple of pages and inspect them,
3. check the TOC page numbers resolve.
