"""Microsoft Graph mail backend.

Authenticates with the client credentials flow against Entra ID and delivers
through the Graph sendMail endpoint. Attachments below the inline limit ride
along as base64 fileAttachment entries; larger ones go through a draft message
with an upload session and chunked PUTs, then the draft is sent.
"""

import base64
import time

import httpx2

from gca.mail.backend import Attachment, MailDeliveryError, OutgoingMail
from gca.services.settings import GraphMailConfig

LOGIN_ROOT = "https://login.microsoftonline.com"
GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
_SCOPE = "https://graph.microsoft.com/.default"
_INLINE_LIMIT_BYTES = 3_145_728
_CHUNK_BYTES = 3_145_728
_TOKEN_SAFETY_SECONDS = 60.0


def _raise_on_error(response: httpx2.Response, action: str) -> None:
    if response.status_code >= 400:
        raise MailDeliveryError(
            f"graph {action} failed ({response.status_code}): {response.text[:300]}"
        )


def _message_payload(
    mail: OutgoingMail, attachments: list[Attachment]
) -> dict[str, object]:
    message: dict[str, object] = {
        "subject": mail.subject,
        "body": {"contentType": "HTML", "content": mail.html_body},
        "toRecipients": [
            {"emailAddress": {"address": address}} for address in mail.recipients
        ],
    }
    if attachments:
        message["attachments"] = [
            {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": attachment.filename,
                "contentType": attachment.mime,
                "contentBytes": base64.b64encode(attachment.content).decode("ascii"),
            }
            for attachment in attachments
        ]
    return message


class GraphBackend:
    def __init__(
        self,
        config: GraphMailConfig,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        self._transport = transport
        self._access_token = ""
        self._token_deadline = 0.0

    async def send(self, mail: OutgoingMail) -> None:
        try:
            async with httpx2.AsyncClient(
                timeout=30.0, transport=self._transport
            ) as client:
                headers = {"Authorization": f"Bearer {await self._token(client)}"}
                small = [
                    a for a in mail.attachments if len(a.content) < _INLINE_LIMIT_BYTES
                ]
                large = [
                    a for a in mail.attachments if len(a.content) >= _INLINE_LIMIT_BYTES
                ]
                message = _message_payload(mail, small)
                if large:
                    await self._send_via_draft(client, headers, message, large)
                else:
                    await self._send_direct(client, headers, message)
        except httpx2.HTTPError as exc:
            raise MailDeliveryError(f"graph request failed: {exc}") from exc

    async def _token(self, client: httpx2.AsyncClient) -> str:
        if self._access_token and time.monotonic() < self._token_deadline:
            return self._access_token
        response = await client.post(
            f"{LOGIN_ROOT}/{self._config.tenant_id}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self._config.client_id,
                "client_secret": self._config.client_secret,
                "scope": _SCOPE,
            },
        )
        _raise_on_error(response, "token request")
        payload: dict[str, object] = response.json()
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise MailDeliveryError("graph token response has no access_token")
        try:
            expires_in = float(str(payload.get("expires_in", 0)))
        except ValueError:
            expires_in = 0.0
        self._access_token = token
        self._token_deadline = time.monotonic() + expires_in - _TOKEN_SAFETY_SECONDS
        return token

    async def _send_direct(
        self,
        client: httpx2.AsyncClient,
        headers: dict[str, str],
        message: dict[str, object],
    ) -> None:
        response = await client.post(
            f"{GRAPH_ROOT}/users/{self._config.sender}/sendMail",
            headers=headers,
            json={"message": message},
        )
        _raise_on_error(response, "sendMail")

    async def _send_via_draft(
        self,
        client: httpx2.AsyncClient,
        headers: dict[str, str],
        message: dict[str, object],
        large: list[Attachment],
    ) -> None:
        base = f"{GRAPH_ROOT}/users/{self._config.sender}"
        response = await client.post(f"{base}/messages", headers=headers, json=message)
        _raise_on_error(response, "draft creation")
        draft: dict[str, object] = response.json()
        draft_id = draft.get("id")
        if not isinstance(draft_id, str) or not draft_id:
            raise MailDeliveryError("graph draft response has no message id")
        for attachment in large:
            await self._upload_attachment(client, headers, base, draft_id, attachment)
        response = await client.post(
            f"{base}/messages/{draft_id}/send", headers=headers
        )
        _raise_on_error(response, "draft send")

    async def _upload_attachment(
        self,
        client: httpx2.AsyncClient,
        headers: dict[str, str],
        base: str,
        draft_id: str,
        attachment: Attachment,
    ) -> None:
        total = len(attachment.content)
        response = await client.post(
            f"{base}/messages/{draft_id}/attachments/createUploadSession",
            headers=headers,
            json={
                "AttachmentItem": {
                    "attachmentType": "file",
                    "name": attachment.filename,
                    "contentType": attachment.mime,
                    "size": total,
                }
            },
        )
        _raise_on_error(response, f"upload session for {attachment.filename}")
        payload: dict[str, object] = response.json()
        upload_url = payload.get("uploadUrl")
        if not isinstance(upload_url, str) or not upload_url:
            raise MailDeliveryError("graph upload session has no uploadUrl")
        for start in range(0, total, _CHUNK_BYTES):
            end = min(start + _CHUNK_BYTES, total) - 1
            response = await client.put(
                upload_url,
                headers={"Content-Range": f"bytes {start}-{end}/{total}"},
                content=attachment.content[start : end + 1],
            )
            _raise_on_error(response, f"chunk upload for {attachment.filename}")
