"""Research: saved analysis compositions over people and repositories."""

from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from gca.charts import render_echarts
from gca.db.engine import get_session
from gca.models import Org, Person, Repo, Research
from gca.services import audit, membership, research
from gca.web.context import RANGE_CHOICES, get_scope, parse_range
from gca.web.deps import templates

router = APIRouter()

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def _picker_options(
    session: AsyncSession,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """(person options, repo options) for the composer selects."""
    persons = (
        (await session.execute(sa.select(Person).order_by(Person.display_name)))
        .scalars()
        .all()
    )
    relevant = await membership.relevant_person_ids(session)
    if relevant is not None:
        persons = [p for p in persons if p.id in relevant]
    person_options = [(str(p.id), p.display_name) for p in persons]
    repo_rows = (
        await session.execute(
            sa.select(Repo.id, Repo.name, Org.login)
            .join(Org, Repo.org_id == Org.id)
            .where(Repo.included.is_(True))
            .order_by(Org.login, Repo.name)
        )
    ).all()
    repo_options = [(str(r.id), f"{r.login}/{r.name}") for r in repo_rows]
    return person_options, repo_options


def _ops_config() -> dict[str, dict[str, object]]:
    return {
        key: {"slots": list(op.slots), "info": op.description, "label": op.label}
        for key, op in research.OPS.items()
    }


@router.get("/research", response_class=HTMLResponse)
async def research_list(request: Request, session: SessionDep) -> Response:
    scope = await get_scope(request, session)
    researches = (
        (
            await session.execute(
                sa.select(Research).order_by(Research.updated_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return templates.TemplateResponse(
        request,
        "research.html",
        {"scope": scope, "researches": researches, "mail_error": None},
    )


@router.post("/research")
async def research_create(
    session: SessionDep, name: Annotated[str, Form()]
) -> RedirectResponse:
    clean = name.strip()[:200]
    if not clean:
        return RedirectResponse(
            "/research?msg=Give the research a name", status_code=303
        )
    row = Research(name=clean, blocks=[])
    session.add(row)
    await session.flush()
    await audit.record(
        session,
        kind="research.created",
        subject=clean,
        message=f"Research '{clean}' created",
    )
    await session.commit()
    return RedirectResponse(f"/research/{row.id}", status_code=303)


@router.get("/research/{research_id}", response_class=HTMLResponse)
async def research_detail(
    request: Request, session: SessionDep, research_id: int
) -> Response:
    row = await session.get(Research, research_id)
    if row is None:
        return RedirectResponse(
            "/research?msg=That research no longer exists", status_code=303
        )
    scope = await get_scope(request, session)
    period = parse_range(request)
    all_time = period.key == "all"
    results = []
    for block in list(row.blocks or []):
        results.append(
            await research.run_block(
                session,
                block,
                orgs=scope.selected_ids,
                start=period.start,
                end=period.end,
                period_label=period.label,
                all_time=all_time,
            )
        )
    person_options, repo_options = await _picker_options(session)
    return templates.TemplateResponse(
        request,
        "research_detail.html",
        {
            "scope": scope,
            "period": period,
            "range_choices": RANGE_CHOICES,
            "research": row,
            "results": results,
            "person_options": person_options,
            "repo_options": repo_options,
            "op_defs": research.OPS,
            "ops_config": _ops_config(),
            "metric_choices": research.METRIC_CHOICES,
            "category_choices": [
                (key, label)
                for key, (label, _rule) in research.SPOTLIGHT_CATEGORIES.items()
            ],
            "mail_error": None,
        },
    )


@router.post("/research/{research_id}/rename")
async def research_rename(
    session: SessionDep, research_id: int, name: Annotated[str, Form()]
) -> RedirectResponse:
    row = await session.get(Research, research_id)
    if row is None:
        return RedirectResponse(
            "/research?msg=That research no longer exists", status_code=303
        )
    clean = name.strip()[:200]
    if clean:
        row.name = clean
        await session.commit()
    return RedirectResponse(f"/research/{research_id}?msg=Renamed", status_code=303)


@router.post("/research/{research_id}/delete")
async def research_delete(session: SessionDep, research_id: int) -> RedirectResponse:
    row = await session.get(Research, research_id)
    if row is not None:
        await audit.record(
            session,
            kind="research.deleted",
            subject=row.name,
            message=f"Research '{row.name}' deleted",
        )
        await session.delete(row)
        await session.commit()
    return RedirectResponse("/research?msg=Research deleted", status_code=303)


@router.post("/research/{research_id}/blocks")
async def research_add_block(
    request: Request, session: SessionDep, research_id: int
) -> RedirectResponse:
    row = await session.get(Research, research_id)
    if row is None:
        return RedirectResponse(
            "/research?msg=That research no longer exists", status_code=303
        )
    form = await request.form()
    op = research.OPS.get(str(form.get("op", "")))
    if op is None:
        return RedirectResponse(
            f"/research/{research_id}?msg=Pick an operation", status_code=303
        )
    block: dict[str, object] = {"op": op.key}
    slots = list(op.slots)
    if "entity" in slots:
        block["entity"] = str(form.get("entity", ""))
    for slot in ("people", "people_b", "repos"):
        if slot in slots:
            block[slot] = [
                int(str(v)) for v in form.getlist(slot) if str(v).strip().isdigit()
            ]
    for slot in ("person", "person_b", "repo", "repo_b"):
        if slot in slots:
            value = str(form.get(slot, ""))
            if value.isdigit():
                block[slot] = int(value)
    if "metric" in slots:
        block["metric"] = str(form.get("metric", ""))
    if "category" in slots:
        block["category"] = str(form.get("category", ""))
    problem = research.validate_block(block)
    if problem is not None:
        return RedirectResponse(
            f"/research/{research_id}?msg={problem}", status_code=303
        )
    # Reassign instead of mutating: plain JSON columns do not track
    # in-place changes.
    row.blocks = [*(row.blocks or []), block]
    await session.commit()
    return RedirectResponse(f"/research/{research_id}?msg=Block added", status_code=303)


@router.post("/research/{research_id}/blocks/{index}/delete")
async def research_delete_block(
    session: SessionDep, research_id: int, index: int
) -> RedirectResponse:
    row = await session.get(Research, research_id)
    if row is None:
        return RedirectResponse(
            "/research?msg=That research no longer exists", status_code=303
        )
    blocks = list(row.blocks or [])
    if 0 <= index < len(blocks):
        blocks.pop(index)
        row.blocks = blocks
        await session.commit()
    return RedirectResponse(
        f"/research/{research_id}?msg=Block removed", status_code=303
    )


@router.get("/api/charts/research")
async def chart_research(
    request: Request,
    session: SessionDep,
    research_id: int,
    block: int,
    chart: int = 0,
) -> JSONResponse:
    row = await session.get(Research, research_id)
    blocks = list(row.blocks or []) if row else []
    if not (0 <= block < len(blocks)):
        return JSONResponse({}, status_code=404)
    scope = await get_scope(request, session)
    period = parse_range(request)
    result = await research.run_block(
        session,
        blocks[block],
        orgs=scope.selected_ids,
        start=period.start,
        end=period.end,
        period_label=period.label,
        all_time=period.key == "all",
    )
    if not (0 <= chart < len(result.charts)):
        return JSONResponse({}, status_code=404)
    return JSONResponse(render_echarts(result.charts[chart]))
