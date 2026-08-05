import base64
import json
import smtplib
import urllib.parse
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path
from typing import ClassVar

import httpx2
import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gca.db.base import Base
from gca.mail import dispatch
from gca.mail.backend import Attachment, MailDeliveryError, OutgoingMail
from gca.mail.graph import GraphBackend
from gca.mail.smtp import SmtpBackend
from gca.models import Report
from gca.services.settings import GraphMailConfig, SettingsStore, SmtpMailConfig

CHUNK = 3_145_728


@pytest.fixture(autouse=True)
def _secret_key(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("APP_SECRET_KEY", Fernet.generate_key().decode())


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def _graph_config() -> GraphMailConfig:
    return GraphMailConfig(
        client_id="cid",
        client_secret="csecret",
        tenant_id="tid",
        sender="sender@example.com",
    )


def _report(pdf_path: str) -> Report:
    return Report(
        kind="weekly",
        title="Weekly activity",
        period_kind="weekly",
        period_start=datetime(2026, 7, 27, tzinfo=UTC),
        period_end=datetime(2026, 8, 3, tzinfo=UTC),
        pdf_path=pdf_path,
    )


# -- smtp backend -----------------------------------------------------------


class _RecordingSmtp:
    instances: ClassVar[list[_RecordingSmtp]] = []

    def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
        self.host = host
        self.port = port
        self.starttls_calls = 0
        self.login_args: tuple[str, str] | None = None
        self.messages: list[tuple[str | None, list[str], str]] = []
        _RecordingSmtp.instances.append(self)

    def __enter__(self) -> _RecordingSmtp:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def starttls(self) -> None:
        self.starttls_calls += 1

    def login(self, username: str, password: str) -> None:
        self.login_args = (username, password)

    def send_message(
        self,
        message: EmailMessage,
        from_addr: str | None = None,
        to_addrs: list[str] | None = None,
    ) -> None:
        self.messages.append((from_addr, list(to_addrs or []), message.as_string()))


async def test_smtp_backend_builds_mime(monkeypatch: pytest.MonkeyPatch) -> None:
    _RecordingSmtp.instances.clear()
    monkeypatch.setattr(smtplib, "SMTP", _RecordingSmtp)
    backend = SmtpBackend(
        SmtpMailConfig(
            host="mailpit",
            port=1025,
            username="mailer",
            password="hunter2",
            starttls=True,
            sender="reports@example.com",
        )
    )
    await backend.send(
        OutgoingMail(
            subject="Weekly report",
            html_body="<p>hello</p>",
            recipients=["a@example.com", "b@example.com"],
            attachments=[Attachment(filename="report.pdf", content=b"%PDF-1.7 data")],
        )
    )
    assert len(_RecordingSmtp.instances) == 1
    smtp = _RecordingSmtp.instances[0]
    assert smtp.host == "mailpit"
    assert smtp.port == 1025
    assert smtp.starttls_calls == 1
    assert smtp.login_args == ("mailer", "hunter2")
    from_addr, to_addrs, payload = smtp.messages[0]
    assert from_addr == "reports@example.com"
    assert to_addrs == ["a@example.com", "b@example.com"]
    assert "Subject: Weekly report" in payload
    assert "report.pdf" in payload
    assert "multipart/mixed" in payload
    assert "text/html" in payload


async def test_smtp_backend_skips_optional_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _RecordingSmtp.instances.clear()
    monkeypatch.setattr(smtplib, "SMTP", _RecordingSmtp)
    backend = SmtpBackend(
        SmtpMailConfig(host="mailpit", starttls=False, sender="reports@example.com")
    )
    await backend.send(
        OutgoingMail(subject="s", html_body="<p>b</p>", recipients=["a@example.com"])
    )
    smtp = _RecordingSmtp.instances[0]
    assert smtp.starttls_calls == 0
    assert smtp.login_args is None


async def test_smtp_backend_wraps_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    class _BrokenSmtp:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise OSError("connection refused")

    monkeypatch.setattr(smtplib, "SMTP", _BrokenSmtp)
    backend = SmtpBackend(SmtpMailConfig(host="down", sender="reports@example.com"))
    with pytest.raises(MailDeliveryError, match="smtp delivery failed"):
        await backend.send(
            OutgoingMail(subject="s", html_body="b", recipients=["a@example.com"])
        )


# -- graph backend ----------------------------------------------------------


async def test_graph_backend_caches_token_and_sends() -> None:
    token_requests = 0
    send_bodies: list[dict[str, object]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal token_requests
        if request.url.host == "login.microsoftonline.com":
            token_requests += 1
            assert request.url.path == "/tid/oauth2/v2.0/token"
            form = urllib.parse.parse_qs(request.content.decode())
            assert form["grant_type"] == ["client_credentials"]
            assert form["client_id"] == ["cid"]
            assert form["client_secret"] == ["csecret"]
            assert form["scope"] == ["https://graph.microsoft.com/.default"]
            return httpx2.Response(
                200, json={"access_token": "tok", "expires_in": 3599}
            )
        if request.url.path == "/v1.0/users/sender@example.com/sendMail":
            assert request.headers["authorization"] == "Bearer tok"
            send_bodies.append(json.loads(request.content))
            return httpx2.Response(202)
        raise AssertionError(f"unexpected {request.method} {request.url}")

    backend = GraphBackend(_graph_config(), transport=httpx2.MockTransport(handler))
    mail = OutgoingMail(
        subject="Weekly report",
        html_body="<p>hello</p>",
        recipients=["a@example.com"],
        attachments=[Attachment(filename="report.pdf", content=b"pdf-bytes")],
    )
    await backend.send(mail)
    await backend.send(mail)
    assert token_requests == 1
    assert len(send_bodies) == 2
    message = send_bodies[0]["message"]
    assert isinstance(message, dict)
    assert message["subject"] == "Weekly report"
    assert message["body"] == {"contentType": "HTML", "content": "<p>hello</p>"}
    assert message["toRecipients"] == [{"emailAddress": {"address": "a@example.com"}}]
    attachment = message["attachments"][0]
    assert attachment["@odata.type"] == "#microsoft.graph.fileAttachment"
    assert attachment["name"] == "report.pdf"
    assert attachment["contentType"] == "application/pdf"
    assert base64.b64decode(attachment["contentBytes"]) == b"pdf-bytes"


async def test_graph_backend_uploads_large_attachments() -> None:
    big = b"x" * (CHUNK + 10)
    events: list[str] = []
    content_ranges: list[str] = []
    uploaded = bytearray()
    base = "/v1.0/users/sender@example.com"

    def handler(request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if request.url.host == "login.microsoftonline.com":
            events.append("token")
            return httpx2.Response(
                200, json={"access_token": "tok", "expires_in": 3599}
            )
        if request.method == "POST" and path == f"{base}/messages":
            events.append("draft")
            body = json.loads(request.content)
            assert body["subject"] == "Big report"
            assert "attachments" not in body
            return httpx2.Response(201, json={"id": "MSG1"})
        if (
            request.method == "POST"
            and path == f"{base}/messages/MSG1/attachments/createUploadSession"
        ):
            events.append("session")
            item = json.loads(request.content)["AttachmentItem"]
            assert item["name"] == "big.pdf"
            assert item["size"] == len(big)
            return httpx2.Response(
                201, json={"uploadUrl": "https://upload.example.com/session-1"}
            )
        if request.method == "PUT" and request.url.host == "upload.example.com":
            events.append("chunk")
            assert "authorization" not in request.headers
            content_ranges.append(request.headers["content-range"])
            uploaded.extend(request.content)
            return httpx2.Response(200)
        if request.method == "POST" and path == f"{base}/messages/MSG1/send":
            events.append("send")
            assert request.headers["authorization"] == "Bearer tok"
            return httpx2.Response(202)
        raise AssertionError(f"unexpected {request.method} {request.url}")

    backend = GraphBackend(_graph_config(), transport=httpx2.MockTransport(handler))
    await backend.send(
        OutgoingMail(
            subject="Big report",
            html_body="<p>big</p>",
            recipients=["a@example.com"],
            attachments=[Attachment(filename="big.pdf", content=big)],
        )
    )
    assert events == ["token", "draft", "session", "chunk", "chunk", "send"]
    assert content_ranges == [
        f"bytes 0-{CHUNK - 1}/{len(big)}",
        f"bytes {CHUNK}-{len(big) - 1}/{len(big)}",
    ]
    assert bytes(uploaded) == big


async def test_graph_backend_wraps_http_failures() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.host == "login.microsoftonline.com":
            return httpx2.Response(
                200, json={"access_token": "tok", "expires_in": 3599}
            )
        return httpx2.Response(403, json={"error": {"message": "denied"}})

    backend = GraphBackend(_graph_config(), transport=httpx2.MockTransport(handler))
    with pytest.raises(MailDeliveryError, match="sendMail failed \\(403\\)"):
        await backend.send(
            OutgoingMail(subject="s", html_body="b", recipients=["a@example.com"])
        )


# -- dispatch ---------------------------------------------------------------


class _StubBackend:
    def __init__(self) -> None:
        self.sent: list[OutgoingMail] = []

    async def send(self, mail: OutgoingMail) -> None:
        self.sent.append(mail)


async def test_send_report_unconfigured(session: AsyncSession, tmp_path: Path) -> None:
    pdf = tmp_path / "weekly.pdf"
    pdf.write_bytes(b"%PDF-1.7")
    report = _report(str(pdf))
    session.add(report)
    await session.flush()
    ok = await dispatch.send_report(session, report, ["a@example.com"])
    assert ok is False
    assert report.status == "unsent"
    status = await SettingsStore(session).mail_status()
    assert status["last_error"] == "mail is not configured"


async def test_send_report_happy_path(
    session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = tmp_path / "weekly.pdf"
    pdf.write_bytes(b"%PDF-1.7")
    stub = _StubBackend()

    async def fake_resolve(store: SettingsStore) -> _StubBackend:
        return stub

    monkeypatch.setattr(dispatch, "resolve_backend", fake_resolve)
    report = _report(str(pdf))
    session.add(report)
    await session.flush()
    ok = await dispatch.send_report(session, report, ["a@example.com", "b@example.com"])
    assert ok is True
    assert report.status == "emailed"
    assert report.emailed_at is not None
    mail = stub.sent[0]
    assert mail.subject == "Weekly activity"
    assert mail.recipients == ["a@example.com", "b@example.com"]
    assert "2026-07-27" in mail.html_body
    assert mail.attachments[0].filename == "weekly.pdf"
    assert mail.attachments[0].content == b"%PDF-1.7"
    status = await SettingsStore(session).mail_status()
    assert status["last_success"]
    assert status["last_error"] is None
