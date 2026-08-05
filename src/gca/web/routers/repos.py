"""Repository list and detail views."""

from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from gca.db.engine import get_session
from gca.models import Org, Repo
from gca.services import stats
from gca.web.context import RANGE_CHOICES, get_scope, parse_range
from gca.web.deps import templates

router = APIRouter()

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/repos", response_class=HTMLResponse)
async def repo_list(request: Request, session: SessionDep) -> Response:
    scope = await get_scope(request, session)
    period = parse_range(request)
    board = await stats.repo_leaderboard(
        session, orgs=scope.selected_ids, start=period.start, end=period.end
    )
    repo_query = sa.select(Repo, Org.login).join(Org, Repo.org_id == Org.id)
    if scope.selected_ids:
        repo_query = repo_query.where(Repo.org_id.in_(scope.selected_ids))
    all_repos = (await session.execute(repo_query.order_by(Org.login, Repo.name))).all()
    active_ids = {r.repo_id for r in board}
    idle_repos = [
        (repo, login) for repo, login in all_repos if repo.id not in active_ids
    ]
    return templates.TemplateResponse(
        request,
        "repos.html",
        {
            "scope": scope,
            "period": period,
            "range_choices": RANGE_CHOICES,
            "board": board,
            "idle_repos": idle_repos,
            "mail_error": None,
        },
    )


@router.get("/repos/{repo_id}", response_class=HTMLResponse)
async def repo_detail(request: Request, session: SessionDep, repo_id: int) -> Response:
    scope = await get_scope(request, session)
    period = parse_range(request)
    repo = await session.get_one(Repo, repo_id)
    org = await session.get_one(Org, repo.org_id)
    totals = await stats.totals(
        session,
        orgs=[],
        start=period.start,
        end=period.end,
        repo_ids=[repo_id],
    )
    contributors = await stats.repo_contributors(
        session, repo_id=repo_id, orgs=[], start=period.start, end=period.end
    )
    return templates.TemplateResponse(
        request,
        "repo_detail.html",
        {
            "scope": scope,
            "period": period,
            "range_choices": RANGE_CHOICES,
            "repo": repo,
            "org": org,
            "totals": totals,
            "contributors": contributors,
            "mail_error": None,
        },
    )
