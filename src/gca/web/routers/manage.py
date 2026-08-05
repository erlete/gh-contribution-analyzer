"""Identity management: suggestions, merges, unmerges, person filters."""

from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from gca.db.engine import get_session
from gca.identity import suggest
from gca.identity.merge import (
    MergeError,
    merge_persons,
    rename_person,
    unmerge_identity,
)
from gca.models import FilterMode, MergeSuggestion, Org, Person, PersonFilter
from gca.web.context import get_scope
from gca.web.deps import templates

router = APIRouter()

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/manage", response_class=HTMLResponse)
async def manage_view(request: Request, session: SessionDep) -> Response:
    scope = await get_scope(request, session)
    suggestions = (
        (
            await session.execute(
                sa.select(MergeSuggestion)
                .where(MergeSuggestion.status == "pending")
                .order_by(MergeSuggestion.score.desc())
            )
        )
        .scalars()
        .all()
    )
    persons = (
        (
            await session.execute(
                sa.select(Person)
                .options(selectinload(Person.identities))
                .order_by(Person.display_name)
            )
        )
        .scalars()
        .all()
    )
    person_by_id = {p.id: p for p in persons}
    filters = (await session.execute(sa.select(PersonFilter))).scalars().all()
    filters_by_org: dict[int, list[int]] = {}
    for row in filters:
        filters_by_org.setdefault(row.org_id, []).append(row.person_id)
    return templates.TemplateResponse(
        request,
        "manage.html",
        {
            "scope": scope,
            "suggestions": suggestions,
            "persons": persons,
            "person_by_id": person_by_id,
            "filters_by_org": filters_by_org,
            "mail_error": None,
        },
    )


@router.post("/manage/suggest/run")
async def run_suggestions(session: SessionDep) -> RedirectResponse:
    created = await suggest.generate(session)
    await session.commit()
    return RedirectResponse(
        f"/manage?msg={created} new suggestions generated", status_code=303
    )


@router.post("/manage/suggestions/{suggestion_id}/accept")
async def accept_suggestion(
    session: SessionDep, suggestion_id: int
) -> RedirectResponse:
    suggestion = await session.get_one(MergeSuggestion, suggestion_id)
    try:
        await merge_persons(session, suggestion.person_a_id, suggestion.person_b_id)
        await session.commit()
        message = "Merged"
    except MergeError as exc:
        await session.rollback()
        message = f"Merge failed: {exc}"
    return RedirectResponse(f"/manage?msg={message}", status_code=303)


@router.post("/manage/suggestions/{suggestion_id}/dismiss")
async def dismiss_suggestion(
    session: SessionDep, suggestion_id: int
) -> RedirectResponse:
    suggestion = await session.get_one(MergeSuggestion, suggestion_id)
    suggestion.status = "dismissed"
    await session.commit()
    return RedirectResponse("/manage?msg=Suggestion dismissed", status_code=303)


@router.post("/manage/merge")
async def manual_merge(
    session: SessionDep,
    target_id: Annotated[int, Form()],
    source_id: Annotated[int, Form()],
) -> RedirectResponse:
    try:
        await merge_persons(session, target_id, source_id)
        await session.commit()
        message = "Merged"
    except MergeError as exc:
        await session.rollback()
        message = f"Merge failed: {exc}"
    return RedirectResponse(f"/manage?msg={message}", status_code=303)


@router.post("/manage/unmerge/{identity_id}")
async def unmerge(session: SessionDep, identity_id: int) -> RedirectResponse:
    try:
        person = await unmerge_identity(session, identity_id)
        await session.commit()
        message = f"Identity split into new person {person.display_name}"
    except MergeError as exc:
        await session.rollback()
        message = f"Unmerge failed: {exc}"
    return RedirectResponse(f"/manage?msg={message}", status_code=303)


@router.post("/manage/person/{person_id}/rename")
async def rename(
    session: SessionDep,
    person_id: int,
    display_name: Annotated[str, Form()],
) -> RedirectResponse:
    await rename_person(session, person_id, display_name)
    await session.commit()
    return RedirectResponse("/manage?msg=Renamed", status_code=303)


@router.post("/manage/filters/{org_id}")
async def set_person_filters(
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
    await session.commit()
    return RedirectResponse(
        f"/manage?msg=Person filters updated for {org.login}", status_code=303
    )
