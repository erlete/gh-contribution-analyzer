"""Shared template environment and formatting helpers."""

from pathlib import Path

from fastapi.templating import Jinja2Templates

from gca.web.glossary import GLOSSARY


def fmt_int(value: float | int | None) -> str:
    if value is None:
        return "0"
    return f"{int(value):,}"


def fmt_float(value: float | None, digits: int = 1) -> str:
    return f"{(value or 0.0):.{digits}f}"


def fmt_pct(value: float | None) -> str:
    return f"{round((value or 0.0) * 100)}"


def options_from(
    items: object, value_attr: str, label_attr: str
) -> list[tuple[str, str]]:
    """Build (value, label) select options from a list of objects."""
    return [
        (str(getattr(item, value_attr)), str(getattr(item, label_attr)))
        for item in items  # type: ignore[attr-defined]
    ]


def build_templates() -> Jinja2Templates:
    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
    templates.env.filters["int_fmt"] = fmt_int
    templates.env.filters["float_fmt"] = fmt_float
    templates.env.filters["pct"] = fmt_pct
    templates.env.globals["options_from"] = options_from
    templates.env.globals["options_from_names"] = lambda names: [
        (name, name) for name in names
    ]
    templates.env.globals["glossary"] = GLOSSARY
    return templates


templates = build_templates()
