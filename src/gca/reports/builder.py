"""Chart rendering and HTML-to-PDF conversion.

WeasyPrint is imported lazily: it needs Pango system libraries that only
exist in the container image, and nothing else in the app should pay that
import cost or dependency.
"""

import base64
import io
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from gca.services.stats import DayPoint

_TEMPLATES = Path(__file__).parent / "templates"


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES)),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["int_fmt"] = lambda v: f"{int(v or 0):,}"
    env.filters["float_fmt"] = lambda v: f"{(v or 0.0):.1f}"
    env.filters["pct"] = lambda v: f"{round((v or 0.0) * 100)}"
    return env


def trend_chart_data_uri(series: list[DayPoint]) -> str:
    """Line chart of daily commits and significance as a base64 PNG."""
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    fig, ax = plt.subplots(figsize=(8.6, 2.6), dpi=150)
    xs = list(range(len(series)))
    ax.plot(
        xs,
        [p.commits for p in series],
        color="#2f6fb6",
        linewidth=1.4,
        label="Commits",
    )
    ax2 = ax.twinx()
    ax2.plot(
        xs,
        [p.significance for p in series],
        color="#2f9e5f",
        linewidth=1.2,
        label="Significance",
    )
    step = max(1, len(xs) // 10)
    ax.set_xticks(xs[::step])
    ax.set_xticklabels(
        [series[i].day.isoformat() for i in xs[::step]],
        rotation=30,
        ha="right",
        fontsize=5,
    )
    ax.set_ylabel("Commits", fontsize=7)
    ax2.set_ylabel("Significance", fontsize=7)
    for axis in (ax, ax2):
        axis.tick_params(labelsize=6)
        for spine in axis.spines.values():
            spine.set_color("#cccccc")
    ax.grid(color="#eeeeee", linewidth=0.5)
    fig.legend(loc="upper left", fontsize=6, frameon=False)
    fig.tight_layout()
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png")
    plt.close(fig)
    encoded = base64.b64encode(buffer.getvalue()).decode()
    return f"data:image/png;base64,{encoded}"


def render_pdf(template_name: str, context: dict[str, Any]) -> bytes:
    html = _env().get_template(template_name).render(**context)
    from weasyprint import HTML

    return bytes(HTML(string=html).write_pdf())
