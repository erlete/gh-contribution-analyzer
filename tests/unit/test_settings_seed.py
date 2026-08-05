from collections.abc import AsyncIterator

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gca.config import Settings
from gca.db.base import Base
from gca.services.settings import SettingsStore, SmtpMailConfig


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


def _env(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


async def test_seed_graph_takes_priority(session: AsyncSession) -> None:
    store = SettingsStore(session)
    await store.seed_from_env(
        _env(
            mail_azure_client_id="cid",
            mail_azure_client_secret="csecret",
            mail_azure_tenant_id="tid",
            mail_sender_address="noreply@example.com",
            smtp_host="mailpit",
        )
    )
    cfg = await store.mail_config()
    assert cfg is not None and cfg.provider == "graph"
    assert cfg.graph is not None
    assert cfg.graph.client_secret == "csecret"
    raw = await store.get("mail")
    assert raw is not None
    assert raw["graph"]["client_secret_enc"] != "csecret"


async def test_seed_smtp_when_no_graph(session: AsyncSession) -> None:
    store = SettingsStore(session)
    await store.seed_from_env(_env(smtp_host="mailpit", smtp_port=1025))
    cfg = await store.mail_config()
    assert cfg is not None and cfg.provider == "smtp"
    assert cfg.smtp is not None and cfg.smtp.port == 1025


async def test_seed_is_idempotent(session: AsyncSession) -> None:
    store = SettingsStore(session)
    await store.seed_from_env(_env(smtp_host="mailpit"))
    await store.set_mail_smtp(SmtpMailConfig(host="manual-edit"))
    await store.seed_from_env(_env(smtp_host="mailpit"))
    cfg = await store.mail_config()
    assert cfg is not None and cfg.smtp is not None
    assert cfg.smtp.host == "manual-edit"


async def test_seed_nothing_configured(session: AsyncSession) -> None:
    store = SettingsStore(session)
    await store.seed_from_env(_env())
    assert await store.mail_config() is None
    assert await store.ai_config() is None


async def test_ai_roundtrip(session: AsyncSession) -> None:
    store = SettingsStore(session)
    await store.seed_from_env(
        _env(
            ai_service_url="https://ai.example.com/v1",
            ai_service_key="sk-test",
            ai_service_model="qwen-max",
        )
    )
    cfg = await store.ai_config()
    assert cfg is not None
    assert cfg.key == "sk-test"
    assert cfg.model == "qwen-max"
