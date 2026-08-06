"""Operations: what is running now and the full audit trail."""

from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from gca.db.engine import get_session
from gca.models import AuditEvent, Org, Report
from gca.web.context import get_scope
from gca.web.deps import templates

router = APIRouter()

SessionDep = Annotated[AsyncSession, Depends(get_session)]

_HISTORY_LIMIT = 300


async def _running(session: AsyncSession) -> dict[str, object]:
    syncing = (
        (
            await session.execute(
                sa.select(Org).where(Org.sync_status == "running").order_by(Org.login)
            )
        )
        .scalars()
        .all()
    )
    reports = (
        (
            await session.execute(
                sa.select(Report)
                .where(Report.status.in_(("queued", "generating")))
                .order_by(Report.id)
            )
        )
        .scalars()
        .all()
    )
    return {"syncing": syncing, "active_reports": reports}


@router.get("/operations", response_class=HTMLResponse)
async def operations_view(request: Request, session: SessionDep) -> Response:
    scope = await get_scope(request, session)
    kind = request.query_params.get("kind", "")
    kinds = [
        row.kind
        for row in (
            await session.execute(
                sa.select(AuditEvent.kind).distinct().order_by(AuditEvent.kind)
            )
        ).all()
    ]
    query = sa.select(AuditEvent).order_by(AuditEvent.ts.desc()).limit(_HISTORY_LIMIT)
    if kind:
        query = query.where(AuditEvent.kind == kind)
    events = (await session.execute(query)).scalars().all()
    running = await _running(session)
    return templates.TemplateResponse(
        request,
        "operations.html",
        {
            "scope": scope,
            "events": events,
            "kind_options": [("", "All kinds")] + [(k, k) for k in kinds],
            "kind_filter": kind,
            "history_limit": _HISTORY_LIMIT,
            "mail_error": None,
            **running,
        },
    )


@router.get("/partials/operations-running", response_class=HTMLResponse)
async def operations_running(request: Request, session: SessionDep) -> Response:
    running = await _running(session)
    return templates.TemplateResponse(request, "_operations_running.html", running)
