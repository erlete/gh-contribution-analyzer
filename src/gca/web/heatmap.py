"""Calendar heatmap shaping: daily rollups to a GitHub-style day grid.

Server-side only presentation shaping: the template renders plain cells,
so the heatmap needs no chart library and no JavaScript. Intensity levels
quantize each day's significance against the shown period's own nonzero
quartiles, so a quiet team still shows contrast.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from gca.services.stats import DayPoint

# One year of columns: more weeks stop reading as a calendar and the cells
# shrink below usability, so longer periods clip to the most recent year.
MAX_WEEKS = 53


@dataclass
class HeatCell:
    day: date | None
    level: int
    title: str


@dataclass
class Heatmap:
    weeks: list[list[HeatCell]]
    months: list[tuple[int, str]]
    clipped: bool
    active_days: int


def build_heatmap(days: list[DayPoint], *, start: date, end: date) -> Heatmap:
    """Shape daily points into Monday-first week columns over [start, end)."""
    shown_start = start
    clipped = False
    if (end - start).days > MAX_WEEKS * 7:
        shown_start = end - timedelta(days=MAX_WEEKS * 7)
        clipped = True
    first_monday = shown_start - timedelta(days=shown_start.weekday())

    by_day = {p.day: p for p in days if shown_start <= p.day < end}
    nonzero = sorted(p.significance for p in by_day.values() if p.significance > 0)

    def level(value: float) -> int:
        if value <= 0 or not nonzero:
            return 0
        # >= keeps the top band reachable: the busiest day always equals
        # the p75 element, and must land on level 4, not 3.
        rank = 1
        for q in (0.25, 0.5, 0.75):
            if value >= nonzero[min(int(q * len(nonzero)), len(nonzero) - 1)]:
                rank += 1
        return rank

    weeks: list[list[HeatCell]] = []
    months: list[tuple[int, str]] = []
    week_start = first_monday
    previous_month = None
    while week_start < end:
        if week_start >= shown_start or week_start + timedelta(days=6) >= shown_start:
            month = week_start.strftime("%b")
            if month != previous_month:
                months.append((len(weeks), month))
                previous_month = month
        column: list[HeatCell] = []
        for offset in range(7):
            day = week_start + timedelta(days=offset)
            if day < shown_start or day >= end:
                column.append(HeatCell(day=None, level=0, title=""))
                continue
            point = by_day.get(day)
            if point is None:
                column.append(
                    HeatCell(
                        day=day,
                        level=0,
                        title=f"{day.strftime('%d/%m/%Y')}: no activity",
                    )
                )
            else:
                column.append(
                    HeatCell(
                        day=day,
                        level=level(point.significance),
                        title=(
                            f"{day.strftime('%d/%m/%Y')}: {point.commits} commits,"
                            f" {point.significance:.1f} significance"
                        ),
                    )
                )
        weeks.append(column)
        week_start += timedelta(days=7)
    return Heatmap(
        weeks=weeks, months=months, clipped=clipped, active_days=len(nonzero)
    )
