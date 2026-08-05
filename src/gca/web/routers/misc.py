"""Scope cookie, trend data API and lazy insight partial."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from sqlalchemy.ext.asyncio import AsyncSession

from gca.ai.insights import insight_for
from gca.db.engine import get_session
from gca.models import Org, Person, Repo
from gca.services import insight_context, stats
from gca.web.context import SCOPE_COOKIE, get_scope, parse_range
from gca.web.deps import templates

router = APIRouter()

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/scope")
async def set_scope(
    request: Request,
    session: SessionDep,
    orgs: Annotated[list[int] | None, Query()] = None,
    next: str = "/",
) -> RedirectResponse:
    scope = await get_scope(request, session)
    valid = {org.id for org in scope.orgs}
    selected = [i for i in (orgs or []) if i in valid]
    if not selected or len(selected) == len(valid):
        value = ""
    else:
        value = ",".join(str(i) for i in selected)
    target = (
        next if next.startswith("/") and not next.startswith(("//", "/\\")) else "/"
    )
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(
        SCOPE_COOKIE,
        value,
        max_age=365 * 24 * 3600,
        httponly=True,
        samesite="strict",
    )
    return response


@router.get("/api/trend")
async def trend(request: Request, session: SessionDep) -> JSONResponse:
    scope = await get_scope(request, session)
    period = parse_range(request)
    repo_id = request.query_params.get("repo_id", "")
    person_id = request.query_params.get("person_id", "")
    series = await stats.timeseries(
        session,
        orgs=scope.selected_ids,
        start=period.start,
        end=period.end,
        repo_ids=[int(repo_id)] if repo_id.isdigit() else None,
        person_ids=[int(person_id)] if person_id.isdigit() else None,
    )
    return JSONResponse(
        {
            "labels": [p.day.isoformat() for p in series],
            "commits": [p.commits for p in series],
            "additions": [p.additions for p in series],
            "deletions": [p.deletions for p in series],
            "significance": [round(p.significance, 2) for p in series],
        }
    )


@router.get("/partials/insight", response_class=HTMLResponse)
async def insight_partial(
    request: Request, session: SessionDep, view: str = "dashboard"
) -> Response:
    scope = await get_scope(request, session)
    period = parse_range(request)
    repo_id = request.query_params.get("repo_id", "")
    person_id = request.query_params.get("person_id", "")

    if view == "person" and person_id.isdigit():
        person = await session.get(Person, int(person_id))
        context = await insight_context.person_context(
            session,
            person_id=int(person_id),
            display_name=person.display_name if person else f"person {person_id}",
            orgs=scope.selected_ids,
            start=period.start,
            end=period.end,
            orgs_label=scope.label,
            period_label=period.label,
        )
        area = "person"
        subject_key = f":p{person_id}"
    elif view == "repo" and repo_id.isdigit():
        repo = await session.get(Repo, int(repo_id))
        org = await session.get(Org, repo.org_id) if repo else None
        full_name = f"{org.login}/{repo.name}" if repo and org else f"repo {repo_id}"
        context = await insight_context.repo_context(
            session,
            repo_id=int(repo_id),
            full_name=full_name,
            orgs=scope.selected_ids,
            start=period.start,
            end=period.end,
            orgs_label=scope.label,
            period_label=period.label,
        )
        area = "repo"
        subject_key = f":r{repo_id}"
    else:
        context = await insight_context.dashboard_context(
            session,
            orgs=scope.selected_ids,
            start=period.start,
            end=period.end,
            orgs_label=scope.label,
            period_label=period.label,
        )
        area = "dashboard"
        subject_key = ""

    result = await insight_for(
        session,
        view=f"{view}{subject_key}",
        scope_key=scope.key,
        period_key=period.key,
        context=context,
        area=area,
    )
    await session.commit()
    return templates.TemplateResponse(
        request, "_insight.html", {"text": result.text, "ai": result.ai}
    )
