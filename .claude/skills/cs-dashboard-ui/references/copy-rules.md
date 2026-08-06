# Copy rules

- English only, sentence case everywhere: headings, buttons, labels, tags.
  Never Title Case, never ALL CAPS (the type styles handle emphasis).
- No em-dashes anywhere in the product or the repo. Use commas, colons or
  separate sentences.
- Buttons say what they do with a verb: "Save and use SMTP", "Scan for new
  suggestions", "Add recipient". Never bare "OK"/"Submit".
- Helper text under a control explains consequences, not mechanics:
  say what happens after the action, in one or two short sentences.
- Status tags come from `c.status_tag()` and its fixed status → color map;
  do not invent ad-hoc colored tags for states.
- Empty states name the state and the way out: "No recipients yet. Add the
  first one below."
- Insight fallback texts are computed statements: only numbers present in
  the context dict, no speculation, no filler adjectives.
- Dates render ISO (`YYYY-MM-DD`), times as `HH:MM` UTC; durations and
  counts use thousands separators via the formatting filters.

## Info tooltips and the glossary

- Every metric, non-obvious table header, KPI card and analytic panel gets
  an info tooltip. The copy lives once, in `src/gca/web/glossary.py`,
  exposed to templates as the `glossary` global; never hardcode a metric
  definition in a template.
- Render through the components: `c.kpi(..., help=glossary.metric)`,
  table header 4-tuples `("Label", kind, unit, glossary.metric)`, and
  `{{ c.info(glossary.key) }}` inline in `<h2>` headings.
- Tooltip copy targets readers with no engineering background: one or two
  short sentences, plain English, define the jargon the entry itself uses
  ("commits, that is individual saved batches of code changes").
- The `info()` macro carries the text in the trigger's `aria-label`; the
  visible tip is fixed-positioned by `app.js` so `.table-scroll` cannot
  clip it. Do not build title-attribute-only or hover-only alternatives.
