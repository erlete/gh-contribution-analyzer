"""Dashboard view."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from gca.db.engine import get_session
from gca.services import stats
from gca.services.settings import SettingsStore
from gca.web.context import RANGE_CHOICES, get_scope, parse_range
from gca.web.deps import templates

router = APIRouter()

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, session: SessionDep) -> Response:
    scope = await get_scope(request, session)
    period = parse_range(request)
    totals = await stats.totals(
        session, orgs=scope.selected_ids, start=period.start, end=period.end
    )
    people = await stats.person_leaderboard(
        session, orgs=scope.selected_ids, start=period.start, end=period.end
    )
    repos = await stats.repo_leaderboard(
        session, orgs=scope.selected_ids, start=period.start, end=period.end
    )
    mail_status = await SettingsStore(session).mail_status()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "scope": scope,
            "period": period,
            "range_choices": RANGE_CHOICES,
            "totals": totals,
            "top_people": people[:8],
            "top_repos": repos[:8],
            "mail_error": mail_status.get("last_error"),
        },
    )
