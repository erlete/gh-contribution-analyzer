"""One chart definition, two renderers.

Feature code constructs a ChartSpec and renders it with `render_echarts`
(interactive web option object) or `render_matplotlib` (SVG for the PDF).
Hand-written ECharts options or pyplot calls outside this package are a
defect: they would drift from the design tokens.
"""

from gca.charts.echarts import render_echarts
from gca.charts.mpl import render_matplotlib
from gca.charts.spec import ChartSpec, Series

__all__ = ["ChartSpec", "Series", "render_echarts", "render_matplotlib"]
