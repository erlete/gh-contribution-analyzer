"""Calendar heatmap shaping."""

from datetime import date, timedelta

from gca.services.stats import DayPoint
from gca.web.heatmap import MAX_WEEKS, build_heatmap


def _point(day: date, significance: float, commits: int = 1) -> DayPoint:
    return DayPoint(
        day=day,
        commits=commits,
        additions=10,
        deletions=1,
        significance=significance,
    )


def test_weeks_align_to_monday_and_pad_edges() -> None:
    # Wednesday to Wednesday: the first and last columns carry void cells.
    start, end = date(2026, 3, 4), date(2026, 3, 18)
    hm = build_heatmap([_point(date(2026, 3, 5), 4.0)], start=start, end=end)
    assert len(hm.weeks) == 3
    first_column = hm.weeks[0]
    assert first_column[0].day is None  # Monday 2026-03-02 before the period
    assert first_column[2].day == date(2026, 3, 4)
    assert all(len(week) == 7 for week in hm.weeks)


def test_levels_quantize_against_shown_quartiles() -> None:
    start, end = date(2026, 3, 2), date(2026, 3, 30)
    days = [
        _point(date(2026, 3, 2), 1.0),
        _point(date(2026, 3, 3), 2.0),
        _point(date(2026, 3, 4), 3.0),
        _point(date(2026, 3, 5), 100.0),
    ]
    hm = build_heatmap(days, start=start, end=end)
    monday = hm.weeks[0]
    assert monday[0].level >= 1
    assert monday[3].level == 4  # the outlier takes the top band
    assert monday[4].level == 0  # no activity
    assert "no activity" in monday[4].title
    assert hm.active_days == 4


def test_titles_use_day_first_dates() -> None:
    start, end = date(2026, 3, 2), date(2026, 3, 9)
    hm = build_heatmap([_point(date(2026, 3, 2), 4.0)], start=start, end=end)
    assert hm.weeks[0][0].title.startswith("02/03/2026:")


def test_long_periods_clip_to_the_most_recent_year() -> None:
    end = date(2026, 3, 30)
    start = end - timedelta(days=3 * 365)
    old = _point(start + timedelta(days=1), 50.0)
    recent = _point(end - timedelta(days=3), 5.0)
    hm = build_heatmap([old, recent], start=start, end=end)
    assert hm.clipped is True
    assert len(hm.weeks) <= MAX_WEEKS + 1
    shown_days = [c.day for w in hm.weeks for c in w if c.day is not None]
    assert old.day not in shown_days
    assert recent.day in shown_days
    # The old point must not skew the quartiles of the shown window.
    assert hm.active_days == 1
