"""Settings store: in-app only configuration with encrypted secrets."""

from collections.abc import AsyncIterator

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gca.db.base import Base
from gca.services.settings import (
    AIConfig,
    GraphMailConfig,
    SettingsStore,
    SmtpMailConfig,
)


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


async def test_unconfigured_store_has_nothing(session: AsyncSession) -> None:
    store = SettingsStore(session)
    assert await store.mail_config() is None
    assert await store.ai_config() is None


async def test_graph_roundtrip_encrypts_secret(session: AsyncSession) -> None:
    store = SettingsStore(session)
    await store.set_mail_graph(
        GraphMailConfig(
            client_id="cid",
            client_secret="csecret",
            tenant_id="tid",
            sender="noreply@example.com",
        )
    )
    cfg = await store.mail_config()
    assert cfg is not None and cfg.provider == "graph"
    assert cfg.graph is not None
    assert cfg.graph.client_secret == "csecret"
    raw = await store.get("mail")
    assert raw is not None
    assert raw["graph"]["client_secret_enc"] != "csecret"


async def test_smtp_roundtrip(session: AsyncSession) -> None:
    store = SettingsStore(session)
    await store.set_mail_smtp(SmtpMailConfig(host="mailpit", port=1025))
    cfg = await store.mail_config()
    assert cfg is not None and cfg.provider == "smtp"
    assert cfg.smtp is not None and cfg.smtp.port == 1025


async def test_last_saved_backend_wins(session: AsyncSession) -> None:
    store = SettingsStore(session)
    await store.set_mail_graph(
        GraphMailConfig(
            client_id="cid", client_secret="s", tenant_id="t", sender="a@b.c"
        )
    )
    await store.set_mail_smtp(SmtpMailConfig(host="manual-edit"))
    cfg = await store.mail_config()
    assert cfg is not None and cfg.provider == "smtp"
    assert cfg.smtp is not None and cfg.smtp.host == "manual-edit"


async def test_ai_roundtrip(session: AsyncSession) -> None:
    store = SettingsStore(session)
    await store.set_ai(
        AIConfig(url="https://ai.example.com/v1", key="sk-test", model="qwen-max")
    )
    cfg = await store.ai_config()
    assert cfg is not None
    assert cfg.key == "sk-test"
    assert cfg.model == "qwen-max"
    await store.clear_ai()
    assert await store.ai_config() is None


async def test_ai_instructions_roundtrip(session: AsyncSession) -> None:
    store = SettingsStore(session)
    assert await store.ai_instructions() == {
        "dashboard": "",
        "person": "",
        "repo": "",
        "report": "",
    }
    await store.set_ai_instructions(
        {"dashboard": "  focus on totals  ", "person": "", "bogus": "ignored"}
    )
    values = await store.ai_instructions()
    assert values["dashboard"] == "focus on totals"
    assert values["person"] == ""
    assert "bogus" not in values
