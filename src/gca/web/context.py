"""Request context: org scope selection and period parsing."""

from dataclasses import dataclass
from datetime import date, timedelta

import sqlalchemy as sa
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from gca.models import Org

SCOPE_COOKIE = "org_scope"
PERIOD_COOKIE = "period"

RANGE_CHOICES: dict[str, tuple[int | None, str]] = {
    "7d": (7, "Last 7 days"),
    "30d": (30, "Last 30 days"),
    "90d": (90, "Last 90 days"),
    "365d": (365, "Last 365 days"),
    "all": (None, "All time"),
}
DEFAULT_RANGE = "30d"
_EPOCH = date(2008, 1, 1)


@dataclass
class Scope:
    orgs: list[Org]
    selected_ids: list[int]

    @property
    def selected_orgs(self) -> list[Org]:
        if not self.selected_ids:
            return self.orgs
        wanted = set(self.selected_ids)
        return [org for org in self.orgs if org.id in wanted]

    @property
    def key(self) -> str:
        return ",".join(str(i) for i in sorted(self.selected_ids)) or "all"

    @property
    def label(self) -> str:
        if not self.selected_ids:
            return "All orgs"
        names = [org.login for org in self.selected_orgs]
        return ", ".join(names) if names else "All orgs"


@dataclass
class Period:
    start: date
    end: date
    key: str
    label: str


async def get_scope(request: Request, session: AsyncSession) -> Scope:
    orgs = list((await session.execute(sa.select(Org).order_by(Org.login))).scalars())
    raw = request.cookies.get(SCOPE_COOKIE, "")
    valid_ids = {org.id for org in orgs}
    selected = [
        int(part)
        for part in raw.split(",")
        if part.strip().isdigit() and int(part) in valid_ids
    ]
    if len(selected) == len(valid_ids):
        selected = []
    return Scope(orgs=orgs, selected_ids=selected)


def _custom_period(custom_from: str, custom_to: str) -> Period | None:
    today = date.today()
    end = today + timedelta(days=1)
    try:
        start = date.fromisoformat(custom_from)
        if custom_to:
            end = date.fromisoformat(custom_to) + timedelta(days=1)
    except ValueError:
        return None
    label = f"{start.isoformat()} to {(end - timedelta(days=1)).isoformat()}"
    return Period(start=start, end=end, key=f"{start}:{end}", label=label)


def parse_range(request: Request) -> Period:
    """Resolve the selected window: explicit query params win, then the
    period cookie (so the selection survives navigation), then the default."""
    today = date.today()
    end = today + timedelta(days=1)
    custom_from = request.query_params.get("from", "")
    custom_to = request.query_params.get("to", "")
    if custom_from:
        period = _custom_period(custom_from, custom_to)
        if period is not None:
            return period
    key = request.query_params.get("range", "")
    if key not in RANGE_CHOICES:
        cookie = request.cookies.get(PERIOD_COOKIE, "")
        if cookie.startswith("custom:"):
            parts = cookie.split(":", 2)
            period = _custom_period(parts[1], parts[2] if len(parts) > 2 else "")
            if period is not None:
                return period
        key = cookie if cookie in RANGE_CHOICES else DEFAULT_RANGE
    days, label = RANGE_CHOICES[key]
    start = _EPOCH if days is None else today - timedelta(days=days - 1)
    return Period(start=start, end=end, key=key, label=label)
