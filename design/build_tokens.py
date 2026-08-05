"""Generate design artifacts from design/tokens.json.

Pure stdlib, deterministic: the same tokens.json always produces byte
identical output. Four artifacts, each carrying a DO NOT EDIT header:

- src/gca/web/static/css/tokens.css      screen custom properties + @font-face
- src/gca/reports/styles/tokens-print.css  WeasyPrint variant with TTF fonts
- src/gca/web/static/echarts-theme.json  registerable ECharts theme
- src/gca/reports/carbon.mplstyle        matplotlib rcParams mirror

Usage:
    python design/build_tokens.py          regenerate all artifacts
    python design/build_tokens.py --check  exit 1 if any artifact is stale
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TOKENS_PATH = ROOT / "design" / "tokens.json"

SCREEN_CSS = ROOT / "src" / "gca" / "web" / "static" / "css" / "tokens.css"
PRINT_CSS = ROOT / "src" / "gca" / "reports" / "styles" / "tokens-print.css"
ECHARTS_THEME = ROOT / "src" / "gca" / "web" / "static" / "echarts-theme.json"
MPLSTYLE = ROOT / "src" / "gca" / "reports" / "carbon.mplstyle"
PALETTES = ROOT / "src" / "gca" / "charts" / "palettes.py"

CSS_HEADER = """\
/* GENERATED FILE - DO NOT EDIT.
   Source: design/tokens.json (regenerate with `python design/build_tokens.py`). */
"""

MPL_HEADER = """\
# GENERATED FILE - DO NOT EDIT.
# Source: design/tokens.json (regenerate with `python design/build_tokens.py`).
"""

FONTS = [
    ("IBM Plex Sans", 300, "IBMPlexSans-Light"),
    ("IBM Plex Sans", 400, "IBMPlexSans-Regular"),
    ("IBM Plex Sans", 600, "IBMPlexSans-SemiBold"),
    ("IBM Plex Mono", 400, "IBMPlexMono-Regular"),
]

VAR_GROUPS = [
    ("color.background", "bg"),
    ("color.text", "text"),
    ("color.icon", "icon"),
    ("color.border", "border"),
    ("color.interactive", "int"),
    ("color.support", "support"),
    ("color.tag", "tag"),
    ("spacing", "sp"),
    ("radius", "radius"),
]

LAYOUT_VARS = {
    "size-xs": "size-xs",
    "size-sm": "size-sm",
    "size-md": "size-md",
    "size-lg": "size-lg",
    "size-xl": "size-xl",
    "size-2xl": "size-2xl",
    "row-height-compact": "row-compact",
    "row-height-short": "row-short",
    "row-height-default": "row-default",
    "header-height": "header-height",
    "field-height": "field-height",
    "container-max": "container-max",
    "icon-size-01": "icon-01",
    "icon-size-02": "icon-02",
    "focus-outline-width": "focus-width",
    "hairline": "hairline",
    "form-max": "form-max",
    "field-max": "field-max",
}

MOTION_VARS = {
    "duration-fast-01": "dur-fast-01",
    "duration-fast-02": "dur-fast-02",
    "duration-moderate-01": "dur-moderate-01",
    "duration-moderate-02": "dur-moderate-02",
    "duration-slow-01": "dur-slow-01",
    "duration-slow-02": "dur-slow-02",
    "ease-productive-standard": "ease-productive",
    "ease-productive-entrance": "ease-entrance",
    "ease-productive-exit": "ease-exit",
    "ease-expressive-standard": "ease-expressive",
}


def load_tokens() -> dict[str, Any]:
    with TOKENS_PATH.open(encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
    return data


def _group(tokens: dict[str, Any], dotted: str) -> dict[str, Any]:
    node: Any = tokens
    for part in dotted.split("."):
        node = node[part]
    return dict(node)


def _font_faces(tokens: dict[str, Any], relative_dir: str, fmt: str, ext: str) -> str:
    del tokens
    blocks = []
    for family, weight, stem in FONTS:
        blocks.append(
            "@font-face {\n"
            f'  font-family: "{family}";\n'
            "  font-style: normal;\n"
            f"  font-weight: {weight};\n"
            f'  src: url("{relative_dir}/{stem}.{ext}") format("{fmt}");\n'
            "  font-display: block;\n"
            "}"
        )
    return "\n".join(blocks)


def _root_variables(tokens: dict[str, Any]) -> str:
    lines: list[str] = []
    for dotted, prefix in VAR_GROUPS:
        for name, value in _group(tokens, dotted).items():
            lines.append(f"  --{prefix}-{name}: {value};")
    for name, var in LAYOUT_VARS.items():
        lines.append(f"  --{var}: {tokens['layout'][name]};")
    for name, var in MOTION_VARS.items():
        lines.append(f"  --{var}: {tokens['motion'][name]};")
    lines.append(f"  --font-sans: {tokens['type']['family-sans']};")
    lines.append(f"  --font-mono: {tokens['type']['family-mono']};")
    lines.append(f"  --fw-light: {tokens['type']['weight-light']};")
    lines.append(f"  --fw-regular: {tokens['type']['weight-regular']};")
    lines.append(f"  --fw-semibold: {tokens['type']['weight-semibold']};")
    for style, spec in tokens["type"]["styles"].items():
        lines.append(f"  --type-{style}-size: {spec['size']};")
    return ":root {\n" + "\n".join(lines) + "\n}"


def _type_classes(tokens: dict[str, Any]) -> str:
    blocks = []
    for style, spec in tokens["type"]["styles"].items():
        family = (
            "var(--font-mono)" if spec.get("family") == "mono" else "var(--font-sans)"
        )
        blocks.append(
            f".type-{style} {{\n"
            f"  font-family: {family};\n"
            f"  font-size: {spec['size']};\n"
            f"  font-weight: {spec['weight']};\n"
            f"  line-height: {spec['line-height']};\n"
            f"  letter-spacing: {spec['letter-spacing']};\n"
            f"}}"
        )
    return "\n".join(blocks)


def emit_screen_css(tokens: dict[str, Any]) -> str:
    parts = [
        CSS_HEADER,
        _font_faces(tokens, "../fonts/plex", "woff2", "woff2"),
        "",
        _root_variables(tokens),
        "",
        _type_classes(tokens),
    ]
    return "\n".join(parts) + "\n"


def emit_print_css(tokens: dict[str, Any]) -> str:
    parts = [
        CSS_HEADER,
        _font_faces(tokens, "../../web/static/fonts/plex", "truetype", "ttf"),
        "",
        _root_variables(tokens),
        "",
        _type_classes(tokens),
    ]
    return "\n".join(parts) + "\n"


def emit_echarts_theme(tokens: dict[str, Any]) -> str:
    viz = tokens["dataviz"]
    family = "IBM Plex Sans"
    axis_common = {
        "axisLine": {"lineStyle": {"color": viz["axis"]}},
        "axisTick": {"lineStyle": {"color": viz["axis"]}},
        "axisLabel": {"color": viz["tick-label"], "fontFamily": family},
        "splitLine": {"lineStyle": {"color": viz["grid"], "width": 1}},
        "splitArea": {"show": False},
        "nameTextStyle": {"color": viz["tick-label"], "fontFamily": family},
    }
    theme = {
        "_meta": "GENERATED FILE - DO NOT EDIT. Source: design/tokens.json.",
        "color": viz["categorical"]["14"],
        "backgroundColor": "transparent",
        "textStyle": {
            "color": tokens["color"]["text"]["primary"],
            "fontFamily": family,
        },
        "title": {
            "textStyle": {"color": viz["title"], "fontFamily": family},
            "subtextStyle": {"color": viz["tick-label"], "fontFamily": family},
        },
        "legend": {
            "textStyle": {"color": viz["legend-text"], "fontFamily": family},
            "pageTextStyle": {"color": viz["legend-text"]},
        },
        "tooltip": {
            "backgroundColor": viz["tooltip-background"],
            "borderColor": viz["tooltip-border"],
            "textStyle": {"color": viz["tooltip-text"], "fontFamily": family},
        },
        "categoryAxis": axis_common,
        "valueAxis": axis_common,
        "timeAxis": axis_common,
        "logAxis": axis_common,
        "line": {"lineStyle": {"width": 2}, "symbolSize": 5},
        "bar": {"itemStyle": {"borderWidth": 0}},
    }
    return json.dumps(theme, indent=2) + "\n"


def emit_mplstyle(tokens: dict[str, Any]) -> str:
    viz = tokens["dataviz"]

    def hexv(value: str) -> str:
        return value.lstrip("#")

    cycle = ", ".join(f"'{hexv(color)}'" for color in viz["categorical"]["14"])
    lines = [
        MPL_HEADER,
        "figure.facecolor: none",
        "figure.edgecolor: none",
        "savefig.facecolor: none",
        "savefig.edgecolor: none",
        "svg.fonttype: none",
        "font.family: sans-serif",
        "font.sans-serif: IBM Plex Sans",
        "font.size: 9.0",
        f"text.color: {hexv(tokens['color']['text']['primary'])}",
        "axes.facecolor: none",
        f"axes.edgecolor: {hexv(viz['axis'])}",
        f"axes.labelcolor: {hexv(viz['tick-label'])}",
        f"axes.titlecolor: {hexv(viz['title'])}",
        "axes.grid: True",
        "axes.axisbelow: True",
        "axes.spines.top: False",
        "axes.spines.right: False",
        "axes.linewidth: 0.75",
        f"axes.prop_cycle: cycler('color', [{cycle}])",
        f"grid.color: {hexv(viz['grid'])}",
        "grid.linewidth: 0.75",
        f"xtick.color: {hexv(viz['tick-label'])}",
        f"ytick.color: {hexv(viz['tick-label'])}",
        "xtick.labelsize: 9.0",
        "ytick.labelsize: 9.0",
        "lines.linewidth: 1.5",
        "legend.frameon: False",
        f"legend.labelcolor: {hexv(viz['legend-text'])}",
    ]
    return "\n".join(lines) + "\n"


def emit_palettes(tokens: dict[str, Any]) -> str:
    viz = tokens["dataviz"]
    categorical = {int(k): v for k, v in viz["categorical"].items()}
    lines = [
        '"""GENERATED FILE - DO NOT EDIT.',
        "",
        "Source: design/tokens.json (regenerate with `python design/build_tokens.py`).",
        '"""',
        "",
        f"CATEGORICAL: dict[int, list[str]] = {categorical!r}",
        "",
        f"SEQUENTIAL_BLUE: list[str] = {viz['sequential-blue']!r}",
        "",
        f"DIVERGENT: list[str] = {viz['divergent']!r}",
        "",
        f"AXIS = {viz['axis']!r}",
        f"GRID = {viz['grid']!r}",
        f"TICK_LABEL = {viz['tick-label']!r}",
        f"TITLE = {viz['title']!r}",
        f"LEGEND_TEXT = {viz['legend-text']!r}",
        "",
        "",
        "def categorical_for(count: int) -> list[str]:",
        '    """The palette engineered for this many data groups, in order."""',
        "    for size in sorted(CATEGORICAL):",
        "        if count <= size:",
        "            return CATEGORICAL[size][:count] if size == 14 else CATEGORICAL[size]",
        "    return CATEGORICAL[max(CATEGORICAL)]",
    ]
    return "\n".join(lines) + "\n"


def artifacts(tokens: dict[str, Any]) -> dict[Path, str]:
    return {
        SCREEN_CSS: emit_screen_css(tokens),
        PRINT_CSS: emit_print_css(tokens),
        ECHARTS_THEME: emit_echarts_theme(tokens),
        MPLSTYLE: emit_mplstyle(tokens),
        PALETTES: emit_palettes(tokens),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify artifacts are current; exit 1 when stale",
    )
    args = parser.parse_args()
    tokens = load_tokens()
    stale: list[str] = []
    for path, content in artifacts(tokens).items():
        rel = path.relative_to(ROOT).as_posix()
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if args.check:
            if current != content:
                stale.append(rel)
            continue
        if current != content:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8", newline="\n") as fh:
                fh.write(content)
            print(f"wrote {rel}")
        else:
            print(f"unchanged {rel}")
    if stale:
        print("stale artifacts (run `python design/build_tokens.py`):", file=sys.stderr)
        for rel in stale:
            print(f"  {rel}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
