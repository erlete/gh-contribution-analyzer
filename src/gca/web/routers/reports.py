"""Report archive, on-demand generation and periodic schedule toggles."""

from datetime import date, timedelta
from pathlib import Path
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    RedirectResponse,
    Response,
)
from sqlalchemy.ext.asyncio import AsyncSession

from gca.config import get_settings
from gca.db.engine import get_session
from gca.mail.dispatch import send_report
from gca.models import Org, Person, Recipient, Repo, Report, ReportSchedule
from gca.reports.service import REPORT_KINDS, generate_reports
from gca.scheduler.periods import PERIOD_KINDS
from gca.web.context import get_scope
from gca.web.deps import templates

router = APIRouter()

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/reports", response_class=HTMLResponse)
async def reports_view(request: Request, session: SessionDep) -> Response:
    scope = await get_scope(request, session)
    archive = (
        (
            await session.execute(
                sa.select(Report).order_by(Report.generated_at.desc()).limit(100)
            )
        )
        .scalars()
        .all()
    )
    schedules = {
        (s.period_kind, s.report_kind): s
        for s in (await session.execute(sa.select(ReportSchedule))).scalars()
    }
    repos = (
        await session.execute(
            sa.select(Repo, Org.login)
            .join(Org, Repo.org_id == Org.id)
            .where(Repo.included.is_(True))
            .order_by(Org.login, Repo.name)
        )
    ).all()
    persons = (
        (await session.execute(sa.select(Person).order_by(Person.display_name)))
        .scalars()
        .all()
    )
    return templates.TemplateResponse(
        request,
        "reports.html",
        {
            "scope": scope,
            "archive": archive,
            "schedules": schedules,
            "period_kinds": PERIOD_KINDS,
            "report_kinds": REPORT_KINDS,
            "repos": repos,
            "persons": persons,
            "mail_error": None,
        },
    )


@router.post("/reports/generate")
async def reports_generate(
    request: Request,
    session: SessionDep,
    kind: Annotated[str, Form()],
    date_from: Annotated[str, Form()] = "",
    date_to: Annotated[str, Form()] = "",
) -> RedirectResponse:
    scope = await get_scope(request, session)
    form = await request.form()
    repo_ids = [
        int(v)
        for k, v in form.multi_items()
        if k == "repo_ids" and isinstance(v, str) and v.isdigit()
    ]
    person_ids = [
        int(v)
        for k, v in form.multi_items()
        if k == "person_ids" and isinstance(v, str) and v.isdigit()
    ]
    try:
        start = date.fromisoformat(date_from) if date_from else date(2008, 1, 1)
        end = (date.fromisoformat(date_to) if date_to else date.today()) + timedelta(
            days=1
        )
    except ValueError:
        return RedirectResponse("/reports?msg=Invalid dates", status_code=303)
    try:
        reports = await generate_reports(
            session,
            kind=kind,
            org_ids=scope.selected_ids,
            start=start,
            end=end,
            period_kind="custom",
            repo_ids=repo_ids or None,
            person_ids=person_ids or None,
        )
        await session.commit()
        message = f"{len(reports)} report(s) generated"
    except Exception as exc:
        await session.rollback()
        message = f"Generation failed: {exc}"
    return RedirectResponse(f"/reports?msg={message}", status_code=303)


@router.get("/reports/{report_id}/download")
async def reports_download(session: SessionDep, report_id: int) -> Response:
    report = await session.get_one(Report, report_id)
    path = Path(get_settings().reports_dir) / report.pdf_path
    if not path.exists():
        return HTMLResponse("PDF file missing on disk", status_code=404)
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=path.name,
    )


@router.post("/reports/{report_id}/email")
async def reports_email(session: SessionDep, report_id: int) -> RedirectResponse:
    report = await session.get_one(Report, report_id)
    recipients = [
        r.email
        for r in (
            await session.execute(
                sa.select(Recipient).where(Recipient.active.is_(True))
            )
        ).scalars()
    ]
    ok = await send_report(session, report, recipients)
    await session.commit()
    message = (
        f"Sent to {len(recipients)} recipient(s)"
        if ok
        else "Delivery failed or mail unconfigured, report kept as unsent"
    )
    return RedirectResponse(f"/reports?msg={message}", status_code=303)


@router.post("/reports/schedules")
async def reports_schedules(request: Request, session: SessionDep) -> RedirectResponse:
    form = await request.form()
    enabled_keys = {
        k for k, v in form.multi_items() if v == "on" and k.startswith("sched-")
    }
    existing = {
        (s.period_kind, s.report_kind): s
        for s in (await session.execute(sa.select(ReportSchedule))).scalars()
    }
    for period_kind in PERIOD_KINDS:
        for report_kind in REPORT_KINDS:
            key = f"sched-{period_kind}-{report_kind}"
            schedule = existing.get((period_kind, report_kind))
            if schedule is None:
                schedule = ReportSchedule(
                    period_kind=period_kind, report_kind=report_kind
                )
                session.add(schedule)
            schedule.enabled = key in enabled_keys
    await session.commit()
    return RedirectResponse("/reports?msg=Schedules saved", status_code=303)
