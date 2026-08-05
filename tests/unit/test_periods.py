from collections.abc import Callable
from datetime import date

import pytest

from gca.scheduler.periods import (
    PERIOD_KINDS,
    date_span,
    period_bounds,
    period_key,
    period_label,
    previous_period,
)

_REF = date(2026, 1, 1)


def test_period_kinds() -> None:
    assert PERIOD_KINDS == (
        "week",
        "month",
        "trimester",
        "quarter",
        "half_year",
        "year",
    )


@pytest.mark.parametrize(
    ("kind", "ref", "start", "end"),
    [
        # Weeks are ISO weeks starting on Monday. 2026-01-01 falls in ISO
        # week 1 of 2026, which starts Monday 2025-12-29.
        ("week", date(2026, 1, 1), date(2025, 12, 29), date(2026, 1, 5)),
        ("week", date(2025, 12, 29), date(2025, 12, 29), date(2026, 1, 5)),
        ("week", date(2026, 1, 4), date(2025, 12, 29), date(2026, 1, 5)),
        ("week", date(2026, 1, 5), date(2026, 1, 5), date(2026, 1, 12)),
        # 2027-01-01 is in ISO week 53 of 2026, starting 2026-12-28.
        ("week", date(2027, 1, 1), date(2026, 12, 28), date(2027, 1, 4)),
        ("week", date(2026, 12, 28), date(2026, 12, 28), date(2027, 1, 4)),
        # Months, including December wrapping to January.
        ("month", date(2026, 1, 15), date(2026, 1, 1), date(2026, 2, 1)),
        ("month", date(2026, 12, 31), date(2026, 12, 1), date(2027, 1, 1)),
        # Leap year February (2028) versus a common year (2027).
        ("month", date(2028, 2, 15), date(2028, 2, 1), date(2028, 3, 1)),
        ("month", date(2027, 2, 15), date(2027, 2, 1), date(2027, 3, 1)),
        # Trimesters: Jan-Apr, May-Aug, Sep-Dec.
        ("trimester", date(2026, 1, 1), date(2026, 1, 1), date(2026, 5, 1)),
        ("trimester", date(2026, 4, 30), date(2026, 1, 1), date(2026, 5, 1)),
        ("trimester", date(2026, 5, 1), date(2026, 5, 1), date(2026, 9, 1)),
        ("trimester", date(2026, 8, 31), date(2026, 5, 1), date(2026, 9, 1)),
        ("trimester", date(2026, 9, 1), date(2026, 9, 1), date(2027, 1, 1)),
        ("trimester", date(2026, 12, 31), date(2026, 9, 1), date(2027, 1, 1)),
        # Quarters: Jan, Apr, Jul, Oct starts.
        ("quarter", date(2026, 2, 14), date(2026, 1, 1), date(2026, 4, 1)),
        ("quarter", date(2026, 6, 30), date(2026, 4, 1), date(2026, 7, 1)),
        ("quarter", date(2026, 7, 1), date(2026, 7, 1), date(2026, 10, 1)),
        ("quarter", date(2026, 12, 31), date(2026, 10, 1), date(2027, 1, 1)),
        # Half years: Jan 1 and Jul 1 starts.
        ("half_year", date(2026, 3, 15), date(2026, 1, 1), date(2026, 7, 1)),
        ("half_year", date(2026, 6, 30), date(2026, 1, 1), date(2026, 7, 1)),
        ("half_year", date(2026, 7, 1), date(2026, 7, 1), date(2027, 1, 1)),
        ("half_year", date(2026, 12, 31), date(2026, 7, 1), date(2027, 1, 1)),
        # Calendar years.
        ("year", date(2026, 1, 1), date(2026, 1, 1), date(2027, 1, 1)),
        ("year", date(2026, 6, 15), date(2026, 1, 1), date(2027, 1, 1)),
        ("year", date(2026, 12, 31), date(2026, 1, 1), date(2027, 1, 1)),
    ],
)
def test_period_bounds(kind: str, ref: date, start: date, end: date) -> None:
    assert period_bounds(kind, ref) == (start, end)


@pytest.mark.parametrize(
    ("kind", "ref", "start", "end"),
    [
        # First day of January: previous month is December of the prior year.
        ("month", date(2026, 1, 1), date(2025, 12, 1), date(2026, 1, 1)),
        ("month", date(2026, 3, 15), date(2026, 2, 1), date(2026, 3, 1)),
        # First trimester: previous is Sep-Dec of the prior year.
        ("trimester", date(2026, 1, 15), date(2025, 9, 1), date(2026, 1, 1)),
        ("trimester", date(2026, 6, 1), date(2026, 1, 1), date(2026, 5, 1)),
        # Week at year start: previous week is entirely in the prior year.
        ("week", date(2026, 1, 1), date(2025, 12, 22), date(2025, 12, 29)),
        ("week", date(2026, 1, 7), date(2025, 12, 29), date(2026, 1, 5)),
        ("quarter", date(2026, 1, 1), date(2025, 10, 1), date(2026, 1, 1)),
        ("quarter", date(2026, 8, 20), date(2026, 4, 1), date(2026, 7, 1)),
        ("half_year", date(2026, 2, 1), date(2025, 7, 1), date(2026, 1, 1)),
        ("half_year", date(2026, 10, 1), date(2026, 1, 1), date(2026, 7, 1)),
        ("year", date(2026, 5, 5), date(2025, 1, 1), date(2026, 1, 1)),
    ],
)
def test_previous_period(kind: str, ref: date, start: date, end: date) -> None:
    assert previous_period(kind, ref) == (start, end)


@pytest.mark.parametrize("kind", PERIOD_KINDS)
@pytest.mark.parametrize(
    "ref",
    [date(2026, 1, 1), date(2026, 7, 15), date(2026, 12, 31), date(2028, 2, 29)],
)
def test_bounds_invariants(kind: str, ref: date) -> None:
    start, end = period_bounds(kind, ref)
    assert start <= ref < end
    prev_start, prev_end = previous_period(kind, ref)
    assert prev_end == start
    assert prev_start < prev_end


@pytest.mark.parametrize(
    ("kind", "start", "expected"),
    [
        ("week", date(2025, 12, 29), "Week 1 2026"),
        ("week", date(2026, 12, 28), "Week 53 2026"),
        ("month", date(2026, 1, 1), "January 2026"),
        ("month", date(2026, 12, 1), "December 2026"),
        ("trimester", date(2026, 1, 1), "T1 2026 (Jan-Apr)"),
        ("trimester", date(2026, 5, 1), "T2 2026 (May-Aug)"),
        ("trimester", date(2026, 9, 1), "T3 2026 (Sep-Dec)"),
        ("quarter", date(2026, 1, 1), "Q1 2026"),
        ("quarter", date(2026, 4, 1), "Q2 2026"),
        ("quarter", date(2026, 7, 1), "Q3 2026"),
        ("quarter", date(2026, 10, 1), "Q4 2026"),
        ("half_year", date(2026, 1, 1), "H1 2026"),
        ("half_year", date(2026, 7, 1), "H2 2026"),
        ("year", date(2026, 1, 1), "2026"),
    ],
)
def test_period_label(kind: str, start: date, expected: str) -> None:
    assert period_label(kind, start) == expected


@pytest.mark.parametrize(
    ("kind", "start", "expected"),
    [
        ("week", date(2025, 12, 29), "week:2025-12-29"),
        ("month", date(2026, 1, 1), "month:2026-01-01"),
        ("year", date(2026, 1, 1), "year:2026-01-01"),
    ],
)
def test_period_key(kind: str, start: date, expected: str) -> None:
    assert period_key(kind, start) == expected


@pytest.mark.parametrize(
    ("kind", "start", "expected"),
    [
        ("week", date(2025, 12, 29), "2025-12-29 to 2026-01-04"),
        ("month", date(2028, 2, 1), "2028-02-01 to 2028-02-29"),
        ("month", date(2027, 2, 1), "2027-02-01 to 2027-02-28"),
        ("trimester", date(2026, 9, 1), "2026-09-01 to 2026-12-31"),
        ("quarter", date(2026, 4, 1), "2026-04-01 to 2026-06-30"),
        ("half_year", date(2026, 7, 1), "2026-07-01 to 2026-12-31"),
        ("year", date(2026, 1, 1), "2026-01-01 to 2026-12-31"),
    ],
)
def test_date_span(kind: str, start: date, expected: str) -> None:
    assert date_span(kind, start) == expected


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda kind: period_bounds(kind, _REF), id="period_bounds"),
        pytest.param(lambda kind: previous_period(kind, _REF), id="previous_period"),
        pytest.param(lambda kind: period_key(kind, _REF), id="period_key"),
        pytest.param(lambda kind: period_label(kind, _REF), id="period_label"),
        pytest.param(lambda kind: date_span(kind, _REF), id="date_span"),
    ],
)
@pytest.mark.parametrize("kind", ["", "day", "fortnight", "WEEK", "months"])
def test_unknown_kind_raises(call: Callable[[str], object], kind: str) -> None:
    with pytest.raises(ValueError, match="unknown period kind"):
        call(kind)
