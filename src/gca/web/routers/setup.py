"""First-run setup gate."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from gca.db.engine import get_session
from gca.services.orgs import add_org, has_validated_org
from gca.web.deps import templates

router = APIRouter()

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/setup", response_class=HTMLResponse)
async def setup_form(request: Request, session: SessionDep) -> Response:
    if await has_validated_org(session):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "setup.html", {"error": None})


@router.post("/setup", response_class=HTMLResponse)
async def setup_submit(
    request: Request,
    session: SessionDep,
    login: Annotated[str, Form()],
    token: Annotated[str, Form()],
) -> Response:
    try:
        org = await add_org(session, login, token)
        await session.commit()
    except Exception as exc:
        return templates.TemplateResponse(
            request,
            "setup.html",
            {"error": str(exc), "login": login},
            status_code=422,
        )
    return RedirectResponse(
        f"/orgs?msg=Organization {org.login} connected. First sync starts with the next worker cycle, or trigger it now.",
        status_code=303,
    )
