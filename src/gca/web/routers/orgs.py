"""Org administration: add, remove, tokens, repo filters, sync triggers."""

from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from gca.crypto import decrypt_str
from gca.db.engine import get_session
from gca.models import (
    FilterMode,
    Org,
    OrgMember,
    Person,
    PersonFilter,
    Repo,
    RepoFilter,
    SyncRun,
)
from gca.services import audit, membership
from gca.services.orgs import add_org, revalidate, update_token
from gca.services.settings import SettingsStore
from gca.sync.api import GitHubClient
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
    repo_names: dict[int, list[str]] = {}
    for name_row in (
        await session.execute(
            sa.select(Repo.org_id, Repo.name).order_by(Repo.org_id, Repo.name)
        )
    ).all():
        repo_names.setdefault(name_row.org_id, []).append(name_row.name)
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
    persons = (
        (await session.execute(sa.select(Person).order_by(Person.display_name)))
        .scalars()
        .all()
    )
    visible = await membership.visible_person_ids(session)
    if visible is not None:
        persons = [p for p in persons if p.id in visible]
    person_by_id = {p.id: p for p in persons}
    person_filter_entries: dict[int, list[dict[str, str]]] = {}
    for prow in (await session.execute(sa.select(PersonFilter))).scalars().all():
        person = person_by_id.get(prow.person_id)
        person_filter_entries.setdefault(prow.org_id, []).append(
            {
                "value": str(prow.person_id),
                "label": person.display_name if person else str(prow.person_id),
            }
        )
    member_counts: dict[int, int] = {
        row.org_id: row.member_count
        for row in (
            await session.execute(
                sa.select(
                    OrgMember.org_id, sa.func.count().label("member_count")
                ).group_by(OrgMember.org_id)
            )
        ).all()
    }
    return templates.TemplateResponse(
        request,
        "orgs.html",
        {
            "scope": scope,
            "orgs": orgs,
            "repo_counts": repo_counts,
            "filter_names": filter_names,
            "repo_names": repo_names,
            "recent_runs": recent_runs,
            "org_logins": {org.id: org.login for org in orgs},
            "persons": persons,
            "person_filter_entries": person_filter_entries,
            "member_counts": member_counts,
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
        await audit.record(
            session,
            kind="org.added",
            subject=org.login,
            message=f"Organization {org.login} connected, first sync queued",
        )
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
        await audit.record(
            session,
            kind="org.token_updated",
            subject=str(org_id),
            message="Organization token replaced and validated",
        )
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
    await audit.record(
        session,
        kind="sync.requested",
        subject=str(org_id),
        message="Manual sync requested",
    )
    await session.commit()
    return RedirectResponse(
        "/orgs?msg=Sync queued, the worker picks it up within 30 seconds",
        status_code=303,
    )


@router.post("/orgs/{org_id}/toggle-sync")
async def orgs_toggle_sync(session: SessionDep, org_id: int) -> RedirectResponse:
    org = await session.get_one(Org, org_id)
    org.sync_enabled = not org.sync_enabled
    state = "enabled" if org.sync_enabled else "paused"
    await audit.record(
        session,
        kind="org.sync_toggled",
        subject=org.login,
        message=f"Scheduled sync {state} for {org.login}",
    )
    await session.commit()
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
    await audit.record(
        session,
        kind="org.removed",
        subject=login,
        message=f"Organization {login} removed with all derived data",
    )
    await session.commit()
    return RedirectResponse(
        f"/orgs?msg=Organization {login} removed with all derived data;"
        " clone cache is cleaned by the worker maintenance job",
        status_code=303,
    )


@router.post("/orgs/{org_id}/repo-filters")
async def orgs_repo_filters(
    request: Request,
    session: SessionDep,
    org_id: int,
    mode: Annotated[str, Form()],
) -> RedirectResponse:
    org = await session.get_one(Org, org_id)
    org.repo_filter_mode = FilterMode(mode)
    await session.execute(sa.delete(RepoFilter).where(RepoFilter.org_id == org_id))
    form = await request.form()
    listed = {
        value.strip()
        for key, value in form.multi_items()
        if key == "names" and isinstance(value, str) and value.strip()
    }
    for name in sorted(listed):
        session.add(RepoFilter(org_id=org_id, repo_name=name))
    _apply_repo_inclusion(org, await _org_repos(session, org_id), listed)
    await audit.record(
        session,
        kind="policy.repo_filters",
        subject=org.login,
        message=(
            f"Repository filters updated for {org.login}: mode {mode},"
            f" {len(listed)} listed"
        ),
    )
    await session.commit()
    return RedirectResponse(
        f"/orgs?msg=Repository filters updated for {org.login}", status_code=303
    )


def _apply_repo_inclusion(org: Org, repos: list[Repo], listed: set[str]) -> None:
    for repo in repos:
        repo.included = _repo_included(
            repo.name,
            org.repo_filter_mode,
            listed,
            is_fork=repo.is_fork,
            ignore_forks=org.ignore_forks,
        )


async def _org_repos(session: AsyncSession, org_id: int) -> list[Repo]:
    return list(
        (await session.execute(sa.select(Repo).where(Repo.org_id == org_id)))
        .scalars()
        .all()
    )


async def _listed_repo_names(session: AsyncSession, org_id: int) -> set[str]:
    return {
        row.repo_name
        for row in (
            await session.execute(
                sa.select(RepoFilter).where(RepoFilter.org_id == org_id)
            )
        ).scalars()
    }


@router.post("/orgs/{org_id}/toggle-forks")
async def orgs_toggle_forks(session: SessionDep, org_id: int) -> RedirectResponse:
    org = await session.get_one(Org, org_id)
    org.ignore_forks = not org.ignore_forks
    listed = await _listed_repo_names(session, org_id)
    _apply_repo_inclusion(org, await _org_repos(session, org_id), listed)
    state = "ignored everywhere" if org.ignore_forks else "included again"
    await audit.record(
        session,
        kind="policy.forks",
        subject=org.login,
        message=f"Forks of {org.login} are now {state}",
    )
    await session.commit()
    return RedirectResponse(
        f"/orgs?msg=Forks of {org.login} are now {state}", status_code=303
    )


@router.post("/orgs/{org_id}/toggle-members")
async def orgs_toggle_members(session: SessionDep, org_id: int) -> RedirectResponse:
    """Enable or disable the members-only hard filter. Enabling fetches the
    member list immediately so a token without the Members permission fails
    loudly instead of silently hiding everyone."""
    org = await session.get_one(Org, org_id, options=[selectinload(Org.credential)])
    if org.members_only:
        org.members_only = False
        await audit.record(
            session,
            kind="policy.members_only",
            subject=org.login,
            message=f"Members-only disabled for {org.login}",
        )
        await session.commit()
        return RedirectResponse(
            f"/orgs?msg=Members-only disabled for {org.login}, external people"
            " count again",
            status_code=303,
        )
    if org.credential is None:
        return RedirectResponse(
            "/orgs?msg=Cannot enable members-only without a stored token",
            status_code=303,
        )
    try:
        token = decrypt_str(org.credential.token_encrypted)
        async with GitHubClient(token) as client:
            member_infos = await client.org_members(org.login)
        stored = await membership.store_members(
            session, org.id, [(m.login, m.node_id) for m in member_infos]
        )
        org.members_only = True
        await audit.record(
            session,
            kind="policy.members_only",
            subject=org.login,
            message=f"Members-only enabled for {org.login}: {stored} members",
        )
        await session.commit()
        message = (
            f"Members-only enabled for {org.login}: {stored} members,"
            " everyone else is now hidden everywhere"
        )
    except Exception as exc:
        await session.rollback()
        message = f"Members-only not enabled: {exc}"
    return RedirectResponse(f"/orgs?msg={message}", status_code=303)


@router.post("/orgs/{org_id}/person-filters")
async def orgs_person_filters(
    request: Request,
    session: SessionDep,
    org_id: int,
    mode: Annotated[str, Form()],
) -> RedirectResponse:
    org = await session.get_one(Org, org_id)
    form = await request.form()
    person_ids = [
        int(value)
        for key, value in form.multi_items()
        if key == "person_ids" and isinstance(value, str) and value.isdigit()
    ]
    org.person_filter_mode = FilterMode(mode)
    await session.execute(sa.delete(PersonFilter).where(PersonFilter.org_id == org_id))
    for pid in person_ids:
        session.add(PersonFilter(org_id=org_id, person_id=pid))
    await audit.record(
        session,
        kind="policy.person_filters",
        subject=org.login,
        message=(
            f"Person filters updated for {org.login}: mode {mode},"
            f" {len(person_ids)} listed"
        ),
    )
    await session.commit()
    return RedirectResponse(
        f"/orgs?msg=Person filters updated for {org.login}", status_code=303
    )


async def _request_sync(session: AsyncSession, org_id: int) -> None:
    store = SettingsStore(session)
    requests = await store.get(SYNC_REQUESTS_KEY) or {}
    requests[str(org_id)] = utcnow().isoformat()
    await store.set(SYNC_REQUESTS_KEY, requests)
