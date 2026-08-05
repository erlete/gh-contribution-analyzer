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


def render_echarts(spec: ChartSpec) -> dict[str, Any]:
    option: dict[str, Any] = {
        "color": palettes.categorical_for(len(spec.series)),
        "aria": {"enabled": True, "label": {"description": spec.description}},
        "grid": {
            "left": 0,
            "right": 8,
            "top": 36 if len(spec.series) > 1 else 16,
            "bottom": 0,
            "containLabel": True,
        },
        "legend": {"show": len(spec.series) > 1, "top": 0, "left": 0, "icon": "rect"},
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
        }
        if index > 0:
            axis["splitLine"] = {"show": False}
        y_axes.append(axis)
    option["yAxis"] = y_axes
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
            entry["lineStyle"] = {"type": _DASHES[line_index % len(_DASHES)]}
            line_index += 1
        series.append(entry)
    option["series"] = series
    if spec.zoom:
        option["dataZoom"] = [{"type": "inside"}]
    return option
