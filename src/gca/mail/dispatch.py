"""Mail dispatch: backend resolution, test mail and report delivery.

Backends are resolved from the settings store on every call so configuration
changes made in the UI take effect immediately. Delivery outcomes are recorded
in the mail_status settings document for display in the app.
"""

import html
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from gca.config import get_settings
from gca.mail.backend import Attachment, MailBackend, MailDeliveryError, OutgoingMail
from gca.mail.graph import GraphBackend
from gca.mail.smtp import SmtpBackend
from gca.models import Report
from gca.services import audit
from gca.services.settings import SettingsStore
from gca.timeutil import utcnow

_NOT_CONFIGURED = "mail is not configured"


async def resolve_backend(store: SettingsStore) -> MailBackend | None:
    """Build the configured mail backend, or None when mail is unconfigured."""
    cfg = await store.mail_config()
    if cfg is None:
        return None
    if cfg.provider == "graph" and cfg.graph is not None:
        return GraphBackend(cfg.graph)
    if cfg.provider == "smtp" and cfg.smtp is not None:
        return SmtpBackend(cfg.smtp)
    return None


async def send_test(session: AsyncSession, to: str) -> None:
    """Send a small test mail to `to`; raise MailDeliveryError on any problem."""
    store = SettingsStore(session)
    backend = await resolve_backend(store)
    if backend is None:
        await store.set_mail_status(last_error=_NOT_CONFIGURED)
        raise MailDeliveryError(_NOT_CONFIGURED)
    mail = OutgoingMail(
        subject="gh-contribution-analyzer test email",
        html_body=(
            "<p>This is a test email from gh-contribution-analyzer. "
            "Your mail configuration works.</p>"
        ),
        recipients=[to],
    )
    try:
        await backend.send(mail)
    except MailDeliveryError as exc:
        await store.set_mail_status(last_error=str(exc))
        raise
    await store.set_mail_status(last_success=utcnow().isoformat(), last_error=None)


async def send_report(
    session: AsyncSession, report: Report, recipients: list[str]
) -> bool:
    """Email a generated report PDF to the given active recipient addresses.

    Returns True and marks the report as emailed on success. Returns False and
    marks the report as unsent when mail is unconfigured or delivery fails.
    Never raises.
    """
    store = SettingsStore(session)

    async def _fail(error: str) -> bool:
        report.status = "unsent"
        await store.set_mail_status(last_error=error)
        await audit.record(
            session,
            kind="mail.failed",
            actor="worker",
            subject=report.title,
            message=f"Report email failed: {error[:300]}",
        )
        return False

    backend = await resolve_backend(store)
    if backend is None:
        return await _fail(_NOT_CONFIGURED)
    if not recipients:
        return await _fail("report has no active recipients")
    pdf_path = Path(get_settings().reports_dir) / report.pdf_path
    try:
        content = pdf_path.read_bytes()
    except OSError as exc:
        return await _fail(f"cannot read report pdf: {exc}")
    period = f"{report.period_start:%Y-%m-%d} to {report.period_end:%Y-%m-%d}"
    mail = OutgoingMail(
        subject=report.title,
        html_body=(
            f"<p>The report <strong>{html.escape(report.title)}</strong> covering "
            f"the period {period} is attached as a PDF.</p>"
        ),
        recipients=recipients,
        attachments=[Attachment(filename=pdf_path.name, content=content)],
    )
    try:
        await backend.send(mail)
    except MailDeliveryError as exc:
        return await _fail(str(exc))
    report.status = "emailed"
    report.emailed_at = utcnow()
    await store.set_mail_status(last_success=utcnow().isoformat(), last_error=None)
    await audit.record(
        session,
        kind="mail.sent",
        actor="worker",
        subject=report.title,
        message=f"Report emailed to {len(recipients)} recipient(s)",
    )
    return True
