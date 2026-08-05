---
name: cs-design-tokens
description: How to add, change or consume design tokens in this repo. Use whenever a task touches colors, spacing, type, motion, chart palettes or the generated token artifacts.
---

# Design tokens

Single source of truth: `design/tokens.json`, hand-edited, IBM Carbon g90
(dark only, web and PDF). Everything else is generated.

## The pipeline

`uv run python design/build_tokens.py` emits FIVE artifacts. They carry a
DO-NOT-EDIT header; never hand-edit them, never format them:

| Artifact | Consumer |
|---|---|
| `src/gca/web/static/css/tokens.css` | dashboard CSS custom properties |
| `src/gca/reports/styles/tokens-print.css` | WeasyPrint print variables |
| `src/gca/web/static/echarts-theme.json` | `charts.js` theme registration |
| `src/gca/reports/carbon.mplstyle` | matplotlib SVG renderer |
| `src/gca/charts/palettes.py` | Python palette dicts + `categorical_for()` |

`design/build_tokens.py --check` byte-compares all five in CI. If it fails,
someone edited an artifact or a formatter touched it. `palettes.py` is
excluded from ruff via `extend-exclude` in `pyproject.toml` for exactly this
reason; keep any new generated Python file excluded the same way.

## Rules

- Every color, size, font and duration in CSS, templates and Python must
  trace to a token variable. Allowed raw literals are structural only:
  `0`, `50%`, `100%`, and data-driven inline widths (e.g. percentile bars).
- Need a new value? Add a token to `tokens.json`, regenerate, then use the
  emitted variable. Do not inline the value "just for now".
- Vendored upstream data lives in `design/vendor/` with pinned versions
  recorded in `design/vendor/SOURCES.md`. Never bump a vendored version
  without updating SOURCES.md (version + retrieval date).
- Review visual changes on `design/swatch.html` (browser) and the print
  swatch (PDF) before shipping.

## Verification

1. `uv run python design/build_tokens.py --check` passes.
2. `rg` for raw hex/px values in changed CSS/templates: only structural
   literals may appear outside `tokens.css`/`tokens-print.css`.
