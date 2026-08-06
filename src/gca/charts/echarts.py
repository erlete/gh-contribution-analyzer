"""ChartSpec to ECharts option object.

The generated theme (echarts-theme.json) carries colors for axes, labels,
tooltips and typography; this renderer only adds structure and the
categorical palette sized to the series count. Lines also cycle dash
patterns so multi-series charts survive colorblindness and greyscale.
"""

from typing import Any

from gca.charts import palettes
from gca.charts.spec import ChartSpec

_DASHES = ["solid", "dashed", "dotted"]


def _colors(spec: ChartSpec) -> list[str]:
    if spec.palette == "wide":
        sequence = palettes.CATEGORICAL[max(palettes.CATEGORICAL)]
        return [sequence[i % len(sequence)] for i in range(len(spec.series))]
    return palettes.categorical_for(len(spec.series))


def render_echarts(spec: ChartSpec) -> dict[str, Any]:
    legend_shown = len(spec.series) > 1
    option: dict[str, Any] = {
        "color": _colors(spec),
        "aria": {"enabled": True, "label": {"description": spec.description}},
        "grid": {
            "left": 0,
            "right": 8,
            "top": 36 if legend_shown else 16,
            "bottom": 0,
            "containLabel": True,
        },
        "legend": {"show": legend_shown, "top": 0, "left": 0, "icon": "rect"},
        "tooltip": {"trigger": "axis"},
    }
    if spec.kind == "hbar":
        option["tooltip"] = {"trigger": "axis", "axisPointer": {"type": "shadow"}}
        option["xAxis"] = {"type": "value", "name": spec.axes[0]}
        option["yAxis"] = {
            "type": "category",
            "data": spec.labels,
            "inverse": True,
            "axisTick": {"show": False},
            "axisLabel": {"width": 150, "overflow": "truncate"},
        }
        hbar_series: list[dict[str, Any]] = []
        for s in spec.series:
            data: list[Any] = list(s.values)
            if s.item_colors:
                data = [
                    {"value": value, "itemStyle": {"color": color}}
                    for value, color in zip(s.values, s.item_colors, strict=False)
                ]
            hbar_series.append(
                {"name": s.name, "type": "bar", "data": data, "stack": s.stack}
            )
        option["series"] = hbar_series
        return option

    option["xAxis"] = {
        "type": "category",
        "data": spec.labels,
        "axisLabel": {"hideOverlap": True},
        "boundaryGap": spec.kind == "bar" or any(s.kind == "bar" for s in spec.series),
    }
    series_names = {s.name for s in spec.series}
    y_axes: list[dict[str, Any]] = []
    for index, name in enumerate(spec.axes):
        # Axis names that repeat a series name add nothing on screen: the
        # legend already carries them and they collide with it.
        axis: dict[str, Any] = {
            "type": "value",
            "name": "" if name in series_names else name,
            # The name is centered on the axis line by default, which clips
            # at the container edge; anchor it to grow inward instead.
            "nameTextStyle": {"align": "left" if index == 0 else "right"},
        }
        if index > 0:
            axis["splitLine"] = {"show": False}
        y_axes.append(axis)
    option["yAxis"] = y_axes
    # A surviving axis name renders in the strip above the plot, where the
    # legend also lives: give each occupant its own row of headroom.
    named_axis = any(axis["name"] for axis in y_axes)
    option["grid"]["top"] = 16 + (20 if legend_shown else 0) + (20 if named_axis else 0)
    series: list[dict[str, Any]] = []
    line_index = 0
    for s in spec.series:
        entry: dict[str, Any] = {
            "name": s.name,
            "type": "bar" if s.kind == "bar" else "line",
            "data": s.values,
            "yAxisIndex": min(s.axis, len(y_axes) - 1),
        }
        if s.kind == "bar":
            if s.stack:
                entry["stack"] = s.stack
        else:
            entry["symbol"] = "none"
            dash = (
                "solid"
                if spec.palette == "wide"
                else _DASHES[line_index % len(_DASHES)]
            )
            entry["lineStyle"] = {"type": dash}
            line_index += 1
        series.append(entry)
    option["series"] = series
    if spec.zoom:
        option["dataZoom"] = [{"type": "inside"}]
    return option
