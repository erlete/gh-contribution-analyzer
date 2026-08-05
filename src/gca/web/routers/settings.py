"""Settings: mail, AI, recipients."""

from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from gca.ai.client import AIClient, AIError
from gca.db.engine import get_session
from gca.mail.backend import MailDeliveryError
from gca.mail.dispatch import send_test
from gca.models import Recipient
from gca.services.settings import (
    AIConfig,
    GraphMailConfig,
    SettingsStore,
    SmtpMailConfig,
)
from gca.web.context import get_scope
from gca.web.deps import templates

router = APIRouter()

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/settings", response_class=HTMLResponse)
async def settings_view(request: Request, session: SessionDep) -> Response:
    scope = await get_scope(request, session)
    store = SettingsStore(session)
    mail_raw = await store.get("mail") or {}
    ai_raw = await store.get("ai") or {}
    mail_status = await store.mail_status()
    recipients = (
        (await session.execute(sa.select(Recipient).order_by(Recipient.email)))
        .scalars()
        .all()
    )
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "scope": scope,
            "mail_raw": mail_raw,
            "ai_raw": ai_raw,
            "mail_status": mail_status,
            "recipients": recipients,
            "mail_error": mail_status.get("last_error"),
        },
    )


@router.post("/settings/mail/graph")
async def settings_mail_graph(
    session: SessionDep,
    client_id: Annotated[str, Form()],
    tenant_id: Annotated[str, Form()],
    sender: Annotated[str, Form()],
    client_secret: Annotated[str, Form()] = "",
) -> RedirectResponse:
    store = SettingsStore(session)
    if not client_secret:
        current = await store.mail_config()
        if current and current.graph:
            client_secret = current.graph.client_secret
    if not client_secret:
        return RedirectResponse(
            "/settings?msg=Client secret is required", status_code=303
        )
    await store.set_mail_graph(
        GraphMailConfig(
            client_id=client_id.strip(),
            client_secret=client_secret.strip(),
            tenant_id=tenant_id.strip(),
            sender=sender.strip(),
        )
    )
    await session.commit()
    return RedirectResponse(
        "/settings?msg=Microsoft Graph mail configured", status_code=303
    )


@router.post("/settings/mail/smtp")
async def settings_mail_smtp(
    session: SessionDep,
    host: Annotated[str, Form()],
    port: Annotated[int, Form()] = 587,
    username: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    sender: Annotated[str, Form()] = "",
    starttls: Annotated[str, Form()] = "",
) -> RedirectResponse:
    store = SettingsStore(session)
    if not password:
        current = await store.mail_config()
        if current and current.smtp:
            password = current.smtp.password
    await store.set_mail_smtp(
        SmtpMailConfig(
            host=host.strip(),
            port=port,
            username=username.strip(),
            password=password,
            starttls=starttls == "on",
            sender=sender.strip(),
        )
    )
    await session.commit()
    return RedirectResponse("/settings?msg=SMTP mail configured", status_code=303)


@router.post("/settings/mail/test")
async def settings_mail_test(
    session: SessionDep, to: Annotated[str, Form()]
) -> RedirectResponse:
    try:
        await send_test(session, to.strip())
        await session.commit()
        message = f"Test email sent to {to}"
    except MailDeliveryError as exc:
        await session.commit()
        message = f"Test failed: {exc}"
    return RedirectResponse(f"/settings?msg={message}", status_code=303)


@router.post("/settings/ai")
async def settings_ai(
    session: SessionDep,
    url: Annotated[str, Form()] = "",
    key: Annotated[str, Form()] = "",
    model: Annotated[str, Form()] = "",
) -> RedirectResponse:
    store = SettingsStore(session)
    if not url or not model:
        await store.clear_ai()
        await session.commit()
        return RedirectResponse(
            "/settings?msg=AI disabled (insight panels hide)", status_code=303
        )
    if not key:
        current = await store.ai_config()
        if current:
            key = current.key
    await store.set_ai(AIConfig(url=url.strip(), key=key.strip(), model=model.strip()))
    await session.commit()
    return RedirectResponse("/settings?msg=AI endpoint configured", status_code=303)


@router.post("/settings/ai/test")
async def settings_ai_test(session: SessionDep) -> RedirectResponse:
    store = SettingsStore(session)
    config = await store.ai_config()
    if config is None:
        return RedirectResponse("/settings?msg=AI is not configured", status_code=303)
    try:
        async with AIClient(config) as client:
            reply = await client.complete(
                "You are a health check. Reply with exactly: ok",
                "Health check",
                max_tokens=10,
            )
        message = f"AI responded: {reply[:60]}"
    except AIError as exc:
        message = f"AI test failed: {exc}"
    return RedirectResponse(f"/settings?msg={message}", status_code=303)


@router.post("/settings/recipients/add")
async def recipients_add(
    session: SessionDep,
    email: Annotated[str, Form()],
    note: Annotated[str, Form()] = "",
) -> RedirectResponse:
    email = email.strip().lower()
    existing = (
        await session.execute(sa.select(Recipient).where(Recipient.email == email))
    ).scalar_one_or_none()
    if existing is None:
        session.add(Recipient(email=email, note=note.strip() or None))
        await session.commit()
        message = f"Recipient {email} added"
    else:
        message = f"{email} is already on the list"
    return RedirectResponse(f"/settings?msg={message}", status_code=303)


@router.post("/settings/recipients/{recipient_id}/toggle")
async def recipients_toggle(session: SessionDep, recipient_id: int) -> RedirectResponse:
    recipient = await session.get_one(Recipient, recipient_id)
    recipient.active = not recipient.active
    await session.commit()
    return RedirectResponse("/settings?msg=Recipient updated", status_code=303)


@router.post("/settings/recipients/{recipient_id}/delete")
async def recipients_delete(session: SessionDep, recipient_id: int) -> RedirectResponse:
    recipient = await session.get(Recipient, recipient_id)
    if recipient is not None:
        await session.delete(recipient)
        await session.commit()
    return RedirectResponse("/settings?msg=Recipient removed", status_code=303)
