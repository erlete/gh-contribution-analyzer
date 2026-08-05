import json
from collections.abc import AsyncIterator

import httpx2
import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gca.ai import insights
from gca.ai.client import AIClient, AIError
from gca.db.base import Base
from gca.models import Insight
from gca.services.settings import AIConfig, SettingsStore


@pytest.fixture(autouse=True)
def _secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
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


def _ai_config() -> AIConfig:
    return AIConfig(url="https://ai.example.com/", key="sk-test", model="qwen-max")


def _completion(content: str) -> httpx2.Response:
    return httpx2.Response(200, json={"choices": [{"message": {"content": content}}]})


async def test_client_happy_path() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/chat/completions"
        assert request.headers["authorization"] == "Bearer sk-test"
        body = json.loads(request.content)
        assert body["model"] == "qwen-max"
        assert body["messages"][0] == {"role": "system", "content": "sys prompt"}
        assert body["messages"][1] == {"role": "user", "content": "user prompt"}
        assert body["max_tokens"] == 700
        assert body["temperature"] == 0.3
        return _completion("  Two teams shipped 14 PRs this week.  ")

    transport = httpx2.MockTransport(handler)
    async with AIClient(_ai_config(), transport=transport) as client:
        result = await client.complete("sys prompt", "user prompt")
    assert result == "Two teams shipped 14 PRs this week."


async def test_client_raises_after_retry_on_5xx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("gca.ai.client._RETRY_SLEEP", 0.0)
    calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(500, json={"error": "upstream exploded"})

    transport = httpx2.MockTransport(handler)
    async with AIClient(_ai_config(), transport=transport) as client:
        with pytest.raises(AIError):
            await client.complete("sys", "user")
    assert calls == 2


async def test_client_raises_on_missing_choices() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"object": "chat.completion"})

    transport = httpx2.MockTransport(handler)
    async with AIClient(_ai_config(), transport=transport) as client:
        with pytest.raises(AIError):
            await client.complete("sys", "user")


async def test_insight_for_unconfigured_returns_none(session: AsyncSession) -> None:
    result = await insights.insight_for(
        session,
        view="overview",
        scope_key="org:acme",
        period_key="2026-W31",
        context={"commits": 12},
    )
    assert result is None


def _patch_client(monkeypatch: pytest.MonkeyPatch, handler: object) -> None:
    transport = httpx2.MockTransport(handler)  # type: ignore[arg-type]

    def factory(config: AIConfig) -> AIClient:
        return AIClient(config, transport=transport)

    monkeypatch.setattr(insights, "AIClient", factory)


async def test_insight_for_generates_and_stores(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await SettingsStore(session).set_ai(_ai_config())
    calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        assert request.url.path == "/chat/completions"
        return _completion("Alpha leads with 12 commits and 3 reviews.")

    _patch_client(monkeypatch, handler)
    result = await insights.insight_for(
        session,
        view="overview",
        scope_key="org:acme",
        period_key="2026-W31",
        context={"commits": 12, "reviews": 3},
    )
    assert result == "Alpha leads with 12 commits and 3 reviews."
    assert calls == 1
    row = await session.scalar(sa.select(Insight))
    assert row is not None
    assert row.view == "overview"
    assert row.model == "qwen-max"
    assert row.content == result


async def test_insight_for_second_call_hits_cache(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await SettingsStore(session).set_ai(_ai_config())
    calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return _completion("Review load doubled to 18 this period.")

    _patch_client(monkeypatch, handler)
    kwargs: dict[str, object] = {
        "view": "reviews",
        "scope_key": "org:acme",
        "period_key": "2026-07",
        "context": {"reviews": 18},
    }
    first = await insights.insight_for(session, **kwargs)  # type: ignore[arg-type]
    assert first == "Review load doubled to 18 this period."
    assert calls == 1
    second = await insights.insight_for(session, **kwargs)  # type: ignore[arg-type]
    assert second == first
    assert calls == 1


async def test_invalidate_view_deletes_rows(session: AsyncSession) -> None:
    session.add_all(
        [
            Insight(cache_key="k1", view="overview", content="a"),
            Insight(cache_key="k2", view="overview", content="b"),
            Insight(cache_key="k3", view="reviews", content="c"),
        ]
    )
    await session.flush()
    deleted = await insights.invalidate_view(session, "overview")
    assert deleted == 2
    remaining = await session.scalar(sa.select(sa.func.count(Insight.id)))
    assert remaining == 1


def test_cache_key_changes_with_context() -> None:
    base = insights.cache_key("overview", "org:acme", "2026-W31", {"commits": 12})
    same = insights.cache_key("overview", "org:acme", "2026-W31", {"commits": 12})
    other = insights.cache_key("overview", "org:acme", "2026-W31", {"commits": 13})
    assert len(base) == 64
    assert base == same
    assert base != other
