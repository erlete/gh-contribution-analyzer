"""ChartSpec to matplotlib SVG for the PDF renderer.

Styled entirely by the generated carbon.mplstyle; the vendored Plex faces
are registered so SVG text references resolve to the same family WeasyPrint
embeds. Output is SVG (vectors stay sharp at any zoom), with the XML prolog
stripped so it can be inlined into report HTML.
"""

import io
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
from matplotlib import font_manager  # noqa: E402
from matplotlib import pyplot as plt  # noqa: E402

from gca.charts import palettes  # noqa: E402
from gca.charts.spec import ChartSpec  # noqa: E402

_STYLE = Path(__file__).parents[1] / "reports" / "carbon.mplstyle"
_FONT_DIR = Path(__file__).parents[1] / "web" / "static" / "fonts" / "plex"
_DASHES = ["solid", (0, (4, 2)), (0, (1, 2))]
_initialized = False


def _ensure_style() -> None:
    global _initialized
    if _initialized:
        return
    for ttf in sorted(_FONT_DIR.glob("*.ttf")):
        font_manager.fontManager.addfont(str(ttf))
    plt.style.use(str(_STYLE))
    _initialized = True


def render_matplotlib(spec: ChartSpec) -> str:
    _ensure_style()
    if spec.palette == "wide":
        sequence = palettes.CATEGORICAL[max(palettes.CATEGORICAL)]
        colors = [sequence[i % len(sequence)] for i in range(len(spec.series))]
    else:
        colors = palettes.categorical_for(len(spec.series))
    fig, ax = plt.subplots(figsize=(8.6, 2.8))
    axes = [ax]
    if len(spec.axes) > 1 and any(s.axis > 0 for s in spec.series):
        twin = ax.twinx()
        twin.spines.right.set_visible(True)
        twin.grid(False)
        axes.append(twin)

    if spec.kind == "hbar":
        ys = list(range(len(spec.labels)))
        left = [0.0] * len(spec.labels)
        for index, s in enumerate(spec.series):
            ax.barh(
                ys,
                s.values,
                left=left if s.stack else None,
                color=s.item_colors or colors[index % len(colors)],
                label=s.name,
            )
            if s.stack:
                left = [a + b for a, b in zip(left, s.values, strict=False)]
        ax.set_yticks(ys)
        ax.set_yticklabels(spec.labels)
        ax.invert_yaxis()
        ax.set_xlabel(spec.axes[0])
    else:
        xs = list(range(len(spec.labels)))
        bottoms: dict[str, list[float]] = {}
        line_index = 0
        for index, s in enumerate(spec.series):
            target = axes[min(s.axis, len(axes) - 1)]
            color = colors[index % len(colors)]
            if s.kind == "bar":
                bottom = bottoms.get(s.stack or f"_{index}", [0.0] * len(xs))
                target.bar(xs, s.values, bottom=bottom, color=color, label=s.name)
                if s.stack:
                    bottoms[s.stack] = [
                        a + b for a, b in zip(bottom, s.values, strict=False)
                    ]
            else:
                target.plot(
                    xs,
                    s.values,
                    color=color,
                    label=s.name,
                    linestyle=(
                        "solid"
                        if spec.palette == "wide"
                        else _DASHES[line_index % len(_DASHES)]
                    ),
                )
                line_index += 1
        step = max(1, len(xs) // spec.max_ticks) if xs else 1
        ax.set_xticks(xs[::step])
        ax.set_xticklabels(
            [spec.labels[i] for i in xs[::step]], rotation=30, ha="right"
        )
        for axis_obj, name in zip(axes, spec.axes, strict=False):
            axis_obj.set_ylabel(name)

    if len(spec.series) > 1:
        handles = []
        labels = []
        for axis_obj in axes:
            h, item_labels = axis_obj.get_legend_handles_labels()
            handles.extend(h)
            labels.extend(item_labels)
        fig.legend(handles, labels, loc="upper left", ncols=len(labels))
    fig.tight_layout()
    buffer = io.StringIO()
    fig.savefig(buffer, format="svg")
    plt.close(fig)
    svg = buffer.getvalue()
    return svg[svg.index("<svg") :]
