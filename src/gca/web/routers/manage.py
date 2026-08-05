"""Identity management: suggestions, merges, unmerges."""

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
from gca.models import MergeSuggestion, Person
from gca.services import audit
from gca.web.context import get_scope
from gca.web.deps import templates

router = APIRouter()

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/manage", response_class=HTMLResponse)
async def manage_view(request: Request, session: SessionDep) -> Response:
    scope = await get_scope(request, session)
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
    # No visibility filtering here: identity management is the repair
    # surface, so it must show persons that members_only hides from stats
    # (they are the duplicates whose merges recover lost attribution).
    person_by_id = {p.id: p for p in persons}
    suggestions = [
        s
        for s in (
            await session.execute(
                sa.select(MergeSuggestion)
                .where(MergeSuggestion.status == "pending")
                .order_by(MergeSuggestion.score.desc())
            )
        ).scalars()
        if s.person_a_id in person_by_id and s.person_b_id in person_by_id
    ]
    return templates.TemplateResponse(
        request,
        "manage.html",
        {
            "scope": scope,
            "suggestions": suggestions,
            "persons": persons,
            "person_by_id": person_by_id,
            "mail_error": None,
        },
    )


@router.post("/manage/suggest/run")
async def run_suggestions(session: SessionDep) -> RedirectResponse:
    created, removed, merged = await suggest.generate(session)
    await session.commit()
    return RedirectResponse(
        f"/manage?msg={created} new suggestions, {removed} stale removed,"
        f" {merged} trivial pairs merged automatically",
        status_code=303,
    )


@router.post("/manage/suggestions/{suggestion_id}/accept")
async def accept_suggestion(
    session: SessionDep,
    suggestion_id: int,
    keep: Annotated[int, Form()],
) -> RedirectResponse:
    """Merge a suggested pair. `keep` picks the survivor explicitly: the other
    person's identities move onto it and the other person disappears."""
    suggestion = await session.get(MergeSuggestion, suggestion_id)
    if suggestion is None:
        return RedirectResponse(
            "/manage?msg=Suggestion no longer exists, an earlier merge or scan"
            " resolved it",
            status_code=303,
        )
    pair = {suggestion.person_a_id, suggestion.person_b_id}
    if keep not in pair:
        return RedirectResponse(
            "/manage?msg=Merge failed: keep must be one of the suggested pair",
            status_code=303,
        )
    source_id = (pair - {keep}).pop()
    try:
        message = await _merge_with_names(session, target_id=keep, source_id=source_id)
        await session.commit()
    except MergeError as exc:
        await session.rollback()
        message = f"Merge failed: {exc}"
    return RedirectResponse(f"/manage?msg={message}", status_code=303)


@router.post("/manage/suggestions/{suggestion_id}/dismiss")
async def dismiss_suggestion(
    session: SessionDep, suggestion_id: int
) -> RedirectResponse:
    suggestion = await session.get(MergeSuggestion, suggestion_id)
    if suggestion is None:
        return RedirectResponse(
            "/manage?msg=Suggestion no longer exists, an earlier merge or scan"
            " resolved it",
            status_code=303,
        )
    suggestion.status = "dismissed"
    await audit.record(
        session,
        kind="suggestion.dismissed",
        subject=f"pair {suggestion.person_a_id}/{suggestion.person_b_id}",
        message="Merge suggestion dismissed",
    )
    await session.commit()
    return RedirectResponse("/manage?msg=Suggestion dismissed", status_code=303)


async def _merge_with_names(
    session: AsyncSession, *, target_id: int, source_id: int
) -> str:
    """Run the merge and return a message naming absorbed and survivor."""
    source = await session.get(Person, source_id)
    source_name = source.display_name if source else str(source_id)
    target = await merge_persons(session, target_id, source_id)
    await audit.record(
        session,
        kind="identity.merged",
        subject=target.display_name,
        message=f"Merged {source_name} into {target.display_name}",
    )
    return f"Merged {source_name} into {target.display_name}"


@router.post("/manage/merge")
async def manual_merge(
    session: SessionDep,
    target_id: Annotated[int, Form()],
    source_id: Annotated[int, Form()],
) -> RedirectResponse:
    try:
        message = await _merge_with_names(
            session, target_id=target_id, source_id=source_id
        )
        await session.commit()
    except MergeError as exc:
        await session.rollback()
        message = f"Merge failed: {exc}"
    return RedirectResponse(f"/manage?msg={message}", status_code=303)


@router.post("/manage/unmerge/{identity_id}")
async def unmerge(session: SessionDep, identity_id: int) -> RedirectResponse:
    try:
        person = await unmerge_identity(session, identity_id)
        await audit.record(
            session,
            kind="identity.split",
            subject=person.display_name,
            message=f"Identity split into new person {person.display_name}",
        )
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
