"""Org administration: add, remove, tokens, repo filters, sync triggers."""

from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from gca.db.engine import get_session
from gca.models import FilterMode, Org, Repo, RepoFilter, SyncRun
from gca.services.orgs import add_org, revalidate, update_token
from gca.services.settings import SettingsStore
from gca.sync.orchestrator import _repo_included, prune_orphans
from gca.timeutil import utcnow
from gca.web.context import get_scope
from gca.web.deps import templates

router = APIRouter()

SessionDep = Annotated[AsyncSession, Depends(get_session)]

SYNC_REQUESTS_KEY = "sync_requests"


@router.get("/orgs", response_class=HTMLResponse)
async def orgs_view(request: Request, session: SessionDep) -> Response:
    scope = await get_scope(request, session)
    orgs = (
        (
            await session.execute(
                sa.select(Org).options(selectinload(Org.credential)).order_by(Org.login)
            )
        )
        .scalars()
        .all()
    )
    repo_counts: dict[int, int] = {
        row.org_id: row.repo_count
        for row in (
            await session.execute(
                sa.select(Repo.org_id, sa.func.count().label("repo_count")).group_by(
                    Repo.org_id
                )
            )
        ).all()
    }
    filters = (await session.execute(sa.select(RepoFilter))).scalars().all()
    filter_names: dict[int, list[str]] = {}
    for row in filters:
        filter_names.setdefault(row.org_id, []).append(row.repo_name)
    recent_runs = (
        (
            await session.execute(
                sa.select(SyncRun)
                .where(SyncRun.kind == "discovery")
                .order_by(SyncRun.started_at.desc())
                .limit(10)
            )
        )
        .scalars()
        .all()
    )
    return templates.TemplateResponse(
        request,
        "orgs.html",
        {
            "scope": scope,
            "orgs": orgs,
            "repo_counts": repo_counts,
            "filter_names": filter_names,
            "recent_runs": recent_runs,
            "mail_error": None,
        },
    )


@router.post("/orgs/add")
async def orgs_add(
    session: SessionDep,
    login: Annotated[str, Form()],
    token: Annotated[str, Form()],
) -> RedirectResponse:
    try:
        org = await add_org(session, login, token)
        await _request_sync(session, org.id)
        await session.commit()
        message = f"Organization {org.login} connected, sync queued"
    except Exception as exc:
        await session.rollback()
        message = f"Failed: {exc}"
    return RedirectResponse(f"/orgs?msg={message}", status_code=303)


@router.post("/orgs/{org_id}/token")
async def orgs_token(
    session: SessionDep, org_id: int, token: Annotated[str, Form()]
) -> RedirectResponse:
    try:
        await update_token(session, org_id, token)
        await session.commit()
        message = "Token updated and validated"
    except Exception as exc:
        await session.rollback()
        message = f"Token update failed: {exc}"
    return RedirectResponse(f"/orgs?msg={message}", status_code=303)


@router.post("/orgs/{org_id}/revalidate")
async def orgs_revalidate(session: SessionDep, org_id: int) -> RedirectResponse:
    ok = await revalidate(session, org_id)
    await session.commit()
    message = "Token valid" if ok else "Validation failed, org marked degraded"
    return RedirectResponse(f"/orgs?msg={message}", status_code=303)


@router.post("/orgs/{org_id}/sync")
async def orgs_sync_now(session: SessionDep, org_id: int) -> RedirectResponse:
    await _request_sync(session, org_id)
    await session.commit()
    return RedirectResponse(
        "/orgs?msg=Sync queued, the worker picks it up within 30 seconds",
        status_code=303,
    )


@router.post("/orgs/{org_id}/toggle-sync")
async def orgs_toggle_sync(session: SessionDep, org_id: int) -> RedirectResponse:
    org = await session.get_one(Org, org_id)
    org.sync_enabled = not org.sync_enabled
    await session.commit()
    state = "enabled" if org.sync_enabled else "paused"
    return RedirectResponse(f"/orgs?msg=Scheduled sync {state}", status_code=303)


@router.post("/orgs/{org_id}/remove")
async def orgs_remove(session: SessionDep, org_id: int) -> RedirectResponse:
    org = await session.get(Org, org_id)
    if org is None:
        return RedirectResponse("/orgs?msg=Org not found", status_code=303)
    login = org.login
    await session.delete(org)
    await session.flush()
    await prune_orphans(session)
    await session.commit()
    return RedirectResponse(
        f"/orgs?msg=Organization {login} removed with all derived data;"
        " clone cache is cleaned by the worker maintenance job",
        status_code=303,
    )


@router.post("/orgs/{org_id}/repo-filters")
async def orgs_repo_filters(
    session: SessionDep,
    org_id: int,
    mode: Annotated[str, Form()],
    names: Annotated[str, Form()] = "",
) -> RedirectResponse:
    org = await session.get_one(Org, org_id)
    org.repo_filter_mode = FilterMode(mode)
    await session.execute(sa.delete(RepoFilter).where(RepoFilter.org_id == org_id))
    listed = {line.strip() for line in names.splitlines() if line.strip()}
    for name in sorted(listed):
        session.add(RepoFilter(org_id=org_id, repo_name=name))
    repos = (
        (await session.execute(sa.select(Repo).where(Repo.org_id == org_id)))
        .scalars()
        .all()
    )
    for repo in repos:
        repo.included = _repo_included(repo.name, org.repo_filter_mode, listed)
    await session.commit()
    return RedirectResponse(
        f"/orgs?msg=Repository filters updated for {org.login}", status_code=303
    )


async def _request_sync(session: AsyncSession, org_id: int) -> None:
    store = SettingsStore(session)
    requests = await store.get(SYNC_REQUESTS_KEY) or {}
    requests[str(org_id)] = utcnow().isoformat()
    await store.set(SYNC_REQUESTS_KEY, requests)
