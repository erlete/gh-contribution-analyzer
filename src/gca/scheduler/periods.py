"""Pure date math for reporting periods.

Every period is a half-open date interval [start, end) where ``end`` is the
first day after the period. Week periods follow ISO 8601 (Monday start); all
other kinds are aligned to calendar month boundaries.
"""

from datetime import date, timedelta

PERIOD_KINDS: tuple[str, ...] = (
    "week",
    "month",
    "trimester",
    "quarter",
    "half_year",
    "year",
)

_KIND_MONTHS: dict[str, int] = {
    "month": 1,
    "quarter": 3,
    "trimester": 4,
    "half_year": 6,
    "year": 12,
}

_MONTH_NAMES: tuple[str, ...] = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

_TRIMESTER_RANGES: tuple[str, ...] = ("Jan-Apr", "May-Aug", "Sep-Dec")


def _check_kind(kind: str) -> None:
    if kind not in PERIOD_KINDS:
        raise ValueError(f"unknown period kind: {kind!r}")


def _add_months(start: date, months: int) -> date:
    total = start.month - 1 + months
    return date(start.year + total // 12, total % 12 + 1, 1)


def period_bounds(kind: str, ref: date) -> tuple[date, date]:
    """Return the [start, end) bounds of the period containing ``ref``."""
    _check_kind(kind)
    if kind == "week":
        start = ref - timedelta(days=ref.weekday())
        return start, start + timedelta(days=7)
    months = _KIND_MONTHS[kind]
    start = date(ref.year, ((ref.month - 1) // months) * months + 1, 1)
    return start, _add_months(start, months)


def previous_period(kind: str, ref: date) -> tuple[date, date]:
    """Return the bounds of the fully closed period before the one with ``ref``."""
    start, _ = period_bounds(kind, ref)
    return period_bounds(kind, start - timedelta(days=1))


def period_key(kind: str, start: date) -> str:
    """Return a stable identifier for the period starting at ``start``."""
    _check_kind(kind)
    return f"{kind}:{start.isoformat()}"


def period_label(kind: str, start: date) -> str:
    """Return a human readable label for the period starting at ``start``."""
    _check_kind(kind)
    if kind == "week":
        iso = start.isocalendar()
        return f"Week {iso.week} {iso.year}"
    if kind == "month":
        return f"{_MONTH_NAMES[start.month - 1]} {start.year}"
    if kind == "trimester":
        index = (start.month - 1) // 4
        return f"T{index + 1} {start.year} ({_TRIMESTER_RANGES[index]})"
    if kind == "quarter":
        return f"Q{(start.month - 1) // 3 + 1} {start.year}"
    if kind == "half_year":
        return f"H{1 if start.month <= 6 else 2} {start.year}"
    return str(start.year)


def date_span(kind: str, start: date) -> str:
    """Return "start to last-day" (inclusive) for display purposes."""
    _, end = period_bounds(kind, start)
    return f"{start.isoformat()} to {(end - timedelta(days=1)).isoformat()}"
