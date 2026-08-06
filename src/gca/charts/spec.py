"""Renderer-independent chart definition."""

from dataclasses import dataclass, field


@dataclass
class Series:
    name: str
    values: list[float]
    kind: str = "line"  # line | bar
    axis: int = 0  # index into ChartSpec.axes
    stack: str | None = None
    # Optional per-point colors (diverging bars); values must come from the
    # generated palettes module, never be hand-picked.
    item_colors: list[str] | None = None


@dataclass
class ChartSpec:
    """A chart, defined once.

    kind: "line" (category x axis), "bar" (category x axis) or
    "hbar" (horizontal bars, categories on y).
    axes: y-axis titles (one entry per axis; two at most).
    palette: "auto" sizes the Carbon categorical palette to the series
    count and cycles dash patterns (the dashboard look); "wide" walks the
    full 14-color sequence with solid lines so research charts read
    differently from the core dashboards.
    """

    kind: str
    labels: list[str]
    series: list[Series]
    axes: list[str] = field(default_factory=lambda: [""])
    title: str | None = None
    description: str = ""
    max_ticks: int = 12
    zoom: bool = False
    palette: str = "auto"
