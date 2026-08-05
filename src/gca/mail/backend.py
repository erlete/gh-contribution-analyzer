"""Mail backend contract shared by the SMTP and Microsoft Graph providers."""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class Attachment:
    filename: str
    content: bytes
    mime: str = "application/pdf"


@dataclass
class OutgoingMail:
    subject: str
    html_body: str
    recipients: list[str]
    attachments: list[Attachment] = field(default_factory=list)


class MailDeliveryError(RuntimeError):
    pass


class MailBackend(Protocol):
    async def send(self, mail: OutgoingMail) -> None: ...
