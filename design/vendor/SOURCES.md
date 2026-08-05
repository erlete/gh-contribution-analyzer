# Vendored design sources

Retrieved 2026-08-05. Every asset is pinned; never update by hand without
refreshing this record. The token build (`design/build_tokens.py`) reads only
`design/tokens.json`; the files under `design/vendor/` are the recorded
upstream facts that `tokens.json` values are derived from.

## Carbon Design System token data

Extracted from the official npm packages with `carbon/extract.cjs` (one-off,
Node 24.11.1; Node is never required at build or run time). The JSON files are
verbatim dumps of the packages' public exports.

| File | Package | Version | Source tarball |
|---|---|---|---|
| `carbon/g90.json` | `@carbon/themes` | 11.78.0 | https://registry.npmjs.org/@carbon/themes/-/themes-11.78.0.tgz |
| `carbon/colors.json` | `@carbon/colors` | 11.55.0 | https://registry.npmjs.org/@carbon/colors/-/colors-11.55.0.tgz |
| `carbon/type.json` | `@carbon/type` | 11.64.0 | https://registry.npmjs.org/@carbon/type/-/type-11.64.0.tgz |
| `carbon/layout.json` | `@carbon/layout` | 11.56.0 | https://registry.npmjs.org/@carbon/layout/-/layout-11.56.0.tgz |
| `carbon/dataviz.json` | `@carbon/charts` | 1.27.18 | https://registry.npmjs.org/@carbon/charts/-/charts-1.27.18.tgz |

`carbon/dataviz.json` is parsed from the package's
`scss/_color-palette.scss` (kept verbatim as `carbon/color-palette.scss`),
with `getColorValue(hue, grade)` references resolved against
`@carbon/colors` 11.55.0. It contains the categorical palettes keyed by
group count ('1'-'5' and '14', each with numbered option variants), the
light and dark variants, the monochrome and divergent quantize ramps, and
the legend area colors. Upstream leaves two literals inline (`#b28600`
yellow, `#8a3800` / `#ba4e00` orange) pending Carbon color additions.

Component and motion data, same retrieval date:

- `carbon/motion.json`: durations and easing curves from `@carbon/motion`
  11.49.0 (https://registry.npmjs.org/@carbon/motion/-/motion-11.49.0.tgz).
- `carbon/button-tokens.json`, `carbon/tag-tokens.json`,
  `carbon/notification-tokens.json`, `carbon/status-tokens.json`: the g-90
  entries of `scss/generated/_*-tokens.scss` inside `@carbon/themes` 11.78.0
  (raw button SCSS kept as `carbon/button-tokens.scss`). The
  `button-disabled` value `rgba(141, 141, 141, 0.3)` appears only in the raw
  SCSS because the extractor skips parenthesized values.
- Structural facts referenced by `design/tokens.json` but not stored as
  files: tag pill radius 16px (`@carbon/styles` 1.112.0
  `scss/components/tag/_tag.scss`), focus outline 2px
  (`scss/utilities/_focus-outline.scss`), button height 3rem and border
  radius 0 (`scss/components/button/_vars.scss`).

All Carbon packages are Apache-2.0.

## IBM Plex fonts

From the official IBM Plex releases at https://github.com/IBM/plex/releases,
OFL-1.1 licensed (license vendored next to the fonts).

| Family | Release tag | Asset |
|---|---|---|
| IBM Plex Sans | `@ibm/plex-sans@1.1.0` (2024-11-13) | `ibm-plex-sans.zip` |
| IBM Plex Mono | `@ibm/plex-mono@2.5.0` (2026-06-11) | `ibm-plex-mono.zip` |

Vendored at `src/gca/web/static/fonts/plex/`: WOFF2 (browser) and TTF
(WeasyPrint, resolved from the filesystem) for exactly the weights the
Carbon type scale uses, per `@carbon/type` `fontWeights`
(light 300, regular 400, semibold 600) plus Mono regular for code/numeric:

- IBMPlexSans-Light (300), IBMPlexSans-Regular (400), IBMPlexSans-SemiBold (600)
- IBMPlexMono-Regular (400)

## Apache ECharts

`src/gca/web/static/vendor/echarts.min.js`, version 6.1.0, from
https://registry.npmjs.org/echarts/-/echarts-6.1.0.tgz (`dist/echarts.min.js`),
Apache-2.0. Replaces Chart.js (`chart.umd.js`), which is deleted once the
templates are migrated to the ChartSpec pipeline.

## SHA-256

```
80fc0908dac8a8b5d3f37984654e25425270c7645b7104be86a14b6a9e88fd3a  design/vendor/carbon/colors.json
67f9c68028f30c39a0a2d05f1bc53872cc64d73c8b13fd518df7de528a3d1323  design/vendor/carbon/dataviz.json
8ae40277862e9fe94e74348cdb1e71e925b7c5892746158686fcd39607d9610d  design/vendor/carbon/g90.json
28459ec6ff5343591d3ff29a4908d885aa0682599b29bca8006259059ac07f3a  design/vendor/carbon/layout.json
dd26d9dd51ae7b821c9236fe25293726725a79b1e5bd15d69150b42a88eae429  design/vendor/carbon/type.json
b66b25aeb4df84e33199dc21694014d336d222cbd9deb0e5a7c14bd6aa0d0fd0  src/gca/web/static/vendor/echarts.min.js
ba204497f16b6d334cee9d1e963a831b73e3a56e1d6300a8489d18df7214b350  src/gca/web/static/fonts/plex/IBMPlexMono-Regular.woff2
769209c2a0dbf2e3f012c22e4c604100cb3f1e7b8beb0ef77bc7d982d85509cc  src/gca/web/static/fonts/plex/IBMPlexSans-Light.woff2
ba711a3085ff9f27440b6b9c4550cfc47c97bf36591d5da958b975bb3add8c1a  src/gca/web/static/fonts/plex/IBMPlexSans-Regular.woff2
f78048030eab62e860efa39a0df79e2e5581bf122eb95b9bc42c0b8a4988d205  src/gca/web/static/fonts/plex/IBMPlexSans-SemiBold.woff2
7c6fbddca4b700be918f5f6183d9bd4464fa427fe435f0b480d77fe2bb8c5a43  src/gca/web/static/fonts/plex/IBMPlexMono-Regular.ttf
2218b5f3f1fc9d3a793343f45c3be5ee7eae9584a7a52391bb8cf2d37622b3e0  src/gca/web/static/fonts/plex/IBMPlexSans-Light.ttf
975dcda37d80f038dcd143c22e33ca2d97a0cc5a929aace1c749153b0fe1afa5  src/gca/web/static/fonts/plex/IBMPlexSans-Regular.ttf
a20caf8286023a6a7a85e40b1d2a4ae9fc3e3b1f9eda8f4c542dd4986af67bb1  src/gca/web/static/fonts/plex/IBMPlexSans-SemiBold.ttf
```
