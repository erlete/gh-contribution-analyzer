"""People list and person detail views."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from gca.db.engine import get_session
from gca.models import Person
from gca.services import stats
from gca.web.context import RANGE_CHOICES, get_scope, parse_range
from gca.web.deps import templates

router = APIRouter()

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/people", response_class=HTMLResponse)
async def people_list(request: Request, session: SessionDep) -> Response:
    scope = await get_scope(request, session)
    period = parse_range(request)
    board = await stats.person_leaderboard(
        session, orgs=scope.selected_ids, start=period.start, end=period.end
    )
    return templates.TemplateResponse(
        request,
        "people.html",
        {
            "scope": scope,
            "period": period,
            "range_choices": RANGE_CHOICES,
            "board": board,
            "mail_error": None,
        },
    )


@router.get("/people/{person_id}", response_class=HTMLResponse)
async def person_detail(
    request: Request, session: SessionDep, person_id: int
) -> Response:
    scope = await get_scope(request, session)
    period = parse_range(request)
    person = await session.get_one(
        Person, person_id, options=[selectinload(Person.identities)]
    )
    board = await stats.person_leaderboard(
        session, orgs=scope.selected_ids, start=period.start, end=period.end
    )
    me = next((s for s in board if s.person_id == person_id), None)
    split = await stats.person_repo_split(
        session,
        person_id=person_id,
        orgs=scope.selected_ids,
        start=period.start,
        end=period.end,
    )
    return templates.TemplateResponse(
        request,
        "person_detail.html",
        {
            "scope": scope,
            "period": period,
            "range_choices": RANGE_CHOICES,
            "person": person,
            "me": me,
            "population": len(board),
            "split": split,
            "mail_error": None,
        },
    )
