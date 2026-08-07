"""Ramp-up and turnover analytics.

Joiners are people whose first recorded activity in scope falls inside the
selected period; leavers are people whose last activity falls inside it but
stopped more than a grace window before the period's end (otherwise anyone
mid-vacation reads as gone). Onboarding curves measure weeks from first
activity to the first merged pull request and to steady output, where
steady means the first week reaching the person's own median nonzero
weekly significance.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from gca.models import Person
from gca.services import stats

GRACE_DAYS = 28


@dataclass
class RampEntry:
    person_id: int
    display_name: str
    first_day: date
    last_day: date
    active_weeks: int
    weeks_to_first_pr: int | None
    weeks_to_steady: int | None


@dataclass
class RampCohort:
    joiners: list[RampEntry]
    leavers: list[RampEntry]
    median_weeks_to_pr: float | None
    measured: int


def _weeks_between(first: date, later: date) -> int:
    return max(0, (later - first).days // 7)


async def cohort(
    session: AsyncSession, *, orgs: list[int], start: date, end: date
) -> RampCohort:
    spans = await stats.activity_spans(session, orgs=orgs)
    if not spans:
        return RampCohort(joiners=[], leavers=[], median_weeks_to_pr=None, measured=0)
    first_prs = await stats.first_merged_prs(session, orgs=orgs)
    names = await _names(session, list(spans))

    joiner_ids = [p for p, (first, _last) in spans.items() if start <= first < end]
    leaver_cut = end - timedelta(days=GRACE_DAYS)
    # Someone who both joined and went quiet inside the period stays in the
    # joiners table only; "left" is reserved for established people.
    leaver_ids = [
        p
        for p, (_first, last) in spans.items()
        if start <= last < leaver_cut and p not in set(joiner_ids)
    ]

    tracked = sorted(set(joiner_ids) | set(leaver_ids))
    weekly: dict[int, dict[date, dict[str, float]]] = {}
    if tracked:
        earliest = min(spans[p][0] for p in tracked)
        weekly = await stats.weekly_by_entity(
            session,
            orgs=orgs,
            start=earliest,
            end=end,
            by="person",
            person_ids=tracked,
        )

    def entry(person_id: int) -> RampEntry:
        first, last = spans[person_id]
        weeks = weekly.get(person_id, {})
        sig_weeks = sorted(
            (week, values.get("significance", 0.0))
            for week, values in weeks.items()
            if values.get("significance", 0.0) > 0
        )
        steady: int | None = None
        if len(sig_weeks) >= 2:
            level = median(value for _week, value in sig_weeks)
            for week, value in sig_weeks:
                if value >= level:
                    steady = _weeks_between(first, week)
                    break
        first_pr = first_prs.get(person_id)
        return RampEntry(
            person_id=person_id,
            display_name=names.get(person_id, f"person {person_id}"),
            first_day=first,
            last_day=last,
            active_weeks=len(sig_weeks),
            weeks_to_first_pr=(
                _weeks_between(first, first_pr) if first_pr is not None else None
            ),
            weeks_to_steady=steady,
        )

    joiners = sorted(
        (entry(p) for p in joiner_ids), key=lambda e: e.first_day, reverse=True
    )
    leavers = sorted(
        (entry(p) for p in leaver_ids), key=lambda e: e.last_day, reverse=True
    )

    # The benchmark spans the whole scope history, not just this period's
    # joiners: one quarter with a single hire should not swing the median.
    onboarding = [
        _weeks_between(spans[p][0], first_prs[p]) for p in spans if p in first_prs
    ]
    return RampCohort(
        joiners=joiners,
        leavers=leavers,
        median_weeks_to_pr=round(median(onboarding), 1) if onboarding else None,
        measured=len(onboarding),
    )


@dataclass
class PersonRamp:
    first_day: date
    last_day: date
    weeks_to_first_pr: int | None


async def person_ramp(
    session: AsyncSession, *, person_id: int, orgs: list[int]
) -> PersonRamp | None:
    spans = await stats.activity_spans(session, orgs=orgs)
    span = spans.get(person_id)
    if span is None:
        return None
    first_prs = await stats.first_merged_prs(session, orgs=orgs)
    first_pr = first_prs.get(person_id)
    return PersonRamp(
        first_day=span[0],
        last_day=span[1],
        weeks_to_first_pr=(
            _weeks_between(span[0], first_pr) if first_pr is not None else None
        ),
    )


async def _names(session: AsyncSession, ids: list[int]) -> dict[int, str]:
    rows = (
        await session.execute(
            sa.select(Person.id, Person.display_name).where(Person.id.in_(ids))
        )
    ).all()
    return {row.id: row.display_name for row in rows}
