"""In-app settings store.

Operational configuration (mail, AI, general flags) lives in the `settings`
table as JSON documents, managed exclusively from the settings screen; the
environment never configures it. Secrets are encrypted with the deployment
Fernet key before touching the database.
"""

from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from gca.crypto import decrypt_str, encrypt_str
from gca.models import Setting

MAIL_KEY = "mail"
AI_KEY = "ai"
AI_INSTRUCTIONS_KEY = "ai_instructions"
MAIL_STATUS_KEY = "mail_status"

INSIGHT_AREAS = ("dashboard", "person", "repo", "report")


class GraphMailConfig(BaseModel):
    client_id: str
    client_secret: str
    tenant_id: str
    sender: str


class SmtpMailConfig(BaseModel):
    host: str
    port: int = 587
    username: str = ""
    password: str = ""
    starttls: bool = True
    sender: str = ""


class MailConfig(BaseModel):
    provider: Literal["graph", "smtp"]
    graph: GraphMailConfig | None = None
    smtp: SmtpMailConfig | None = None


class AIConfig(BaseModel):
    url: str
    key: str = ""
    model: str


class SettingsStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, key: str) -> dict[str, Any] | None:
        row = await self._session.get(Setting, key)
        value = row.value if row else None
        return dict(value) if isinstance(value, dict) else None

    async def set(self, key: str, value: dict[str, Any]) -> None:
        row = await self._session.get(Setting, key)
        if row is None:
            self._session.add(Setting(key=key, value=value))
        else:
            row.value = value
        await self._session.flush()

    async def mail_config(self) -> MailConfig | None:
        raw = await self.get(MAIL_KEY)
        if not raw or raw.get("provider") not in ("graph", "smtp"):
            return None
        provider = raw["provider"]
        if provider == "graph":
            graph = raw.get("graph") or {}
            if not all(
                graph.get(k)
                for k in ("client_id", "client_secret_enc", "tenant_id", "sender")
            ):
                return None
            return MailConfig(
                provider="graph",
                graph=GraphMailConfig(
                    client_id=graph["client_id"],
                    client_secret=decrypt_str(graph["client_secret_enc"]),
                    tenant_id=graph["tenant_id"],
                    sender=graph["sender"],
                ),
            )
        smtp = raw.get("smtp") or {}
        if not smtp.get("host"):
            return None
        return MailConfig(
            provider="smtp",
            smtp=SmtpMailConfig(
                host=smtp["host"],
                port=int(smtp.get("port", 587)),
                username=smtp.get("username", ""),
                password=(
                    decrypt_str(smtp["password_enc"])
                    if smtp.get("password_enc")
                    else ""
                ),
                starttls=bool(smtp.get("starttls", True)),
                sender=smtp.get("sender", ""),
            ),
        )

    async def set_mail_graph(self, cfg: GraphMailConfig) -> None:
        raw = await self.get(MAIL_KEY) or {}
        raw["provider"] = "graph"
        raw["graph"] = {
            "client_id": cfg.client_id,
            "client_secret_enc": encrypt_str(cfg.client_secret),
            "tenant_id": cfg.tenant_id,
            "sender": cfg.sender,
        }
        await self.set(MAIL_KEY, raw)

    async def set_mail_smtp(self, cfg: SmtpMailConfig) -> None:
        raw = await self.get(MAIL_KEY) or {}
        raw["provider"] = "smtp"
        raw["smtp"] = {
            "host": cfg.host,
            "port": cfg.port,
            "username": cfg.username,
            "password_enc": encrypt_str(cfg.password) if cfg.password else "",
            "starttls": cfg.starttls,
            "sender": cfg.sender,
        }
        await self.set(MAIL_KEY, raw)

    async def ai_config(self) -> AIConfig | None:
        raw = await self.get(AI_KEY)
        if not raw or not raw.get("url") or not raw.get("model"):
            return None
        return AIConfig(
            url=raw["url"],
            key=decrypt_str(raw["key_enc"]) if raw.get("key_enc") else "",
            model=raw["model"],
        )

    async def set_ai(self, cfg: AIConfig) -> None:
        await self.set(
            AI_KEY,
            {
                "url": cfg.url,
                "key_enc": encrypt_str(cfg.key) if cfg.key else "",
                "model": cfg.model,
            },
        )

    async def clear_ai(self) -> None:
        await self.set(AI_KEY, {})

    async def ai_instructions(self) -> dict[str, str]:
        """Operator instructions per insight area. Missing areas map to ''."""
        raw = await self.get(AI_INSTRUCTIONS_KEY) or {}
        return {area: str(raw.get(area, "") or "").strip() for area in INSIGHT_AREAS}

    async def set_ai_instructions(self, values: dict[str, str]) -> None:
        await self.set(
            AI_INSTRUCTIONS_KEY,
            {
                area: values.get(area, "").strip()
                for area in INSIGHT_AREAS
                if values.get(area, "").strip()
            },
        )

    async def mail_status(self) -> dict[str, Any]:
        return await self.get(MAIL_STATUS_KEY) or {}

    async def set_mail_status(self, **fields: Any) -> None:
        status = await self.mail_status()
        status.update(fields)
        await self.set(MAIL_STATUS_KEY, status)
