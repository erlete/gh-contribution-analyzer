"""SMTP mail backend.

smtplib is blocking, so delivery runs in a worker thread via asyncio.to_thread.
The message is a standard MIME multipart: plain-text fallback, HTML alternative
and one binary part per attachment.
"""

import asyncio
import smtplib
from email.message import EmailMessage

from gca.mail.backend import MailDeliveryError, OutgoingMail
from gca.services.settings import SmtpMailConfig

_FALLBACK_TEXT = "This message requires an HTML capable mail client."


class SmtpBackend:
    def __init__(self, config: SmtpMailConfig) -> None:
        self._config = config

    async def send(self, mail: OutgoingMail) -> None:
        message = self._build_message(mail)
        try:
            await asyncio.to_thread(self._deliver, message, mail.recipients)
        except (smtplib.SMTPException, OSError) as exc:
            raise MailDeliveryError(f"smtp delivery failed: {exc}") from exc

    def _build_message(self, mail: OutgoingMail) -> EmailMessage:
        message = EmailMessage()
        message["Subject"] = mail.subject
        message["From"] = self._config.sender
        message["To"] = ", ".join(mail.recipients)
        message.set_content(_FALLBACK_TEXT)
        message.add_alternative(mail.html_body, subtype="html")
        for attachment in mail.attachments:
            maintype, _, subtype = attachment.mime.partition("/")
            message.add_attachment(
                attachment.content,
                maintype=maintype or "application",
                subtype=subtype or "octet-stream",
                filename=attachment.filename,
            )
        return message

    def _deliver(self, message: EmailMessage, recipients: list[str]) -> None:
        with smtplib.SMTP(self._config.host, self._config.port, timeout=30) as smtp:
            if self._config.starttls:
                smtp.starttls()
            if self._config.username:
                smtp.login(self._config.username, self._config.password)
            smtp.send_message(
                message, from_addr=self._config.sender, to_addrs=recipients
            )
