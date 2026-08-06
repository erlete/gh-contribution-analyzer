"""Chart rendering and HTML-to-PDF conversion.

WeasyPrint is imported lazily: it needs Pango system libraries that only
exist in the container image, and nothing else in the app should pay that
import cost or dependency.

Fonts: WeasyPrint only honors @font-face when a FontConfiguration is shared
between the CSS objects and write_pdf; without it the PDF silently falls
back to system fonts. The font check in tests guards this.
"""

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from gca.charts import ChartSpec, Series, render_matplotlib
from gca.services.stats import DayPoint

_TEMPLATES = Path(__file__).parent / "templates"
_STYLES = Path(__file__).parent / "styles"


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES)),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["int_fmt"] = lambda v: f"{int(v or 0):,}"
    env.filters["float_fmt"] = lambda v: f"{(v or 0.0):.1f}"
    env.filters["pct"] = lambda v: f"{round((v or 0.0) * 100)}"
    return env


def trend_chart_svg(series: list[DayPoint]) -> str:
    """Daily commits and significance as an inline SVG (vectors stay sharp
    in the PDF at any zoom)."""
    spec = ChartSpec(
        kind="line",
        labels=[p.day.isoformat() for p in series],
        series=[
            Series(name="Commits", values=[float(p.commits) for p in series]),
            Series(
                name="Significance",
                values=[round(p.significance, 2) for p in series],
                axis=1,
            ),
        ],
        axes=["Commits", "Significance"],
        description="Daily commits and significance over the report period",
    )
    return render_matplotlib(spec)


def render_pdf(template_name: str, context: dict[str, Any]) -> bytes:
    html = _env().get_template(template_name).render(**context)
    from weasyprint import CSS, HTML
    from weasyprint.text.fonts import FontConfiguration

    font_config = FontConfiguration()
    stylesheets = [
        CSS(filename=str(_STYLES / "tokens-print.css"), font_config=font_config),
        CSS(filename=str(_STYLES / "report.css"), font_config=font_config),
    ]
    return bytes(
        HTML(string=html).write_pdf(stylesheets=stylesheets, font_config=font_config)
    )
