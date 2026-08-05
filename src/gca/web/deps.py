"""Shared template environment and formatting helpers."""

from pathlib import Path

from fastapi.templating import Jinja2Templates


def fmt_int(value: float | int | None) -> str:
    if value is None:
        return "0"
    return f"{int(value):,}"


def fmt_float(value: float | None, digits: int = 1) -> str:
    return f"{(value or 0.0):.{digits}f}"


def fmt_pct(value: float | None) -> str:
    return f"{round((value or 0.0) * 100)}"


def build_templates() -> Jinja2Templates:
    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
    templates.env.filters["int_fmt"] = fmt_int
    templates.env.filters["float_fmt"] = fmt_float
    templates.env.filters["pct"] = fmt_pct
    return templates


templates = build_templates()
