"""Scope cookie, trend data API and lazy insight partial."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from markupsafe import escape
from sqlalchemy.ext.asyncio import AsyncSession

from gca.ai.insights import insight_for
from gca.db.engine import get_session
from gca.services import stats
from gca.web.context import SCOPE_COOKIE, get_scope, parse_range

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
) -> HTMLResponse:
    scope = await get_scope(request, session)
    period = parse_range(request)
    repo_id = request.query_params.get("repo_id", "")
    person_id = request.query_params.get("person_id", "")
    subject_key = f":r{repo_id}" if repo_id else f":p{person_id}" if person_id else ""
    totals = await stats.totals(
        session,
        orgs=scope.selected_ids,
        start=period.start,
        end=period.end,
        repo_ids=[int(repo_id)] if repo_id.isdigit() else None,
        person_ids=[int(person_id)] if person_id.isdigit() else None,
    )
    top_people = await stats.person_leaderboard(
        session,
        orgs=scope.selected_ids,
        start=period.start,
        end=period.end,
        repo_ids=[int(repo_id)] if repo_id.isdigit() else None,
        person_ids=[int(person_id)] if person_id.isdigit() else None,
    )
    context: dict[str, object] = {
        "period": period.label,
        "orgs": scope.label,
        "totals": {
            "commits": totals.commits,
            "additions": totals.additions,
            "deletions": totals.deletions,
            "churn_lines": totals.churn,
            "churn_ratio": round(totals.churn_ratio, 3),
            "significance": round(totals.significance, 1),
            "prs_opened": totals.prs_opened,
            "prs_merged": totals.prs_merged,
            "reviews": totals.reviews,
            "active_people": totals.active_people,
            "active_repos": totals.active_repos,
        },
        "top_contributors": [
            {
                "name": s.display_name,
                "commits": s.commits,
                "significance": round(s.significance, 1),
                "churn_ratio": round(s.churn_ratio, 3),
            }
            for s in top_people[:8]
        ],
    }
    text = await insight_for(
        session,
        view=f"{view}{subject_key}",
        scope_key=scope.key,
        period_key=period.key,
        context=context,
    )
    await session.commit()
    if not text:
        return HTMLResponse("")
    return HTMLResponse(
        '<div class="insight"><div class="tag">Insight</div>'
        f"<div>{escape(text)}</div></div>"
    )
