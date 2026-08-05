"""Web app integration: setup gate, org onboarding and every main view."""

from collections.abc import AsyncIterator
from pathlib import Path

import httpx2
import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import create_async_engine

import gca.models  # noqa: F401  (populate Base.metadata before create_all)
from gca.db.base import Base
from gca.sync.api import OrgInfo

pytestmark = pytest.mark.integration


class FakeGitHubClient:
    def __init__(self, token: str, **kwargs: object) -> None:
        self.token = token
        self.rate_snapshot = {"x-ratelimit-remaining": "4999"}
        self.use_rest = False

    async def __aenter__(self) -> FakeGitHubClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def close(self) -> None:
        return None

    async def validate_org(self, login: str) -> OrgInfo:
        if login == "not-an-org":
            from gca.sync.api import NotAnOrgError

            raise NotAnOrgError(f"{login} is a user account")
        return OrgInfo(login=login, name=login.title(), node_id="O_1", avatar_url="")


@pytest.fixture
async def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[httpx2.AsyncClient]:
    db_path = tmp_path / "web.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv("APP_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("CLONE_DIR", str(tmp_path / "clones"))

    from gca.config import get_settings
    from gca.db.engine import reset_engine_cache

    get_settings.cache_clear()
    reset_engine_cache()

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()

    import gca.services.orgs as orgs_service

    monkeypatch.setattr(orgs_service, "GitHubClient", FakeGitHubClient)

    from gca.main import create_app

    app = create_app()
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(
        transport=transport, base_url="http://test", follow_redirects=False
    ) as http:
        yield http


async def test_setup_gate_redirects(client: httpx2.AsyncClient) -> None:
    for path in ("/", "/repos", "/people", "/orgs", "/reports", "/settings"):
        response = await client.get(path)
        assert response.status_code == 303, path
        assert response.headers["location"] == "/setup"
    response = await client.get("/setup")
    assert response.status_code == 200
    assert "First things first" in response.text


async def test_setup_rejects_user_accounts(client: httpx2.AsyncClient) -> None:
    response = await client.post(
        "/setup", data={"login": "not-an-org", "token": "github_pat_x"}
    )
    assert response.status_code == 422
    assert "user account" in response.text


async def test_full_flow_after_setup(client: httpx2.AsyncClient) -> None:
    response = await client.post(
        "/setup", data={"login": "acme", "token": "github_pat_x"}
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/orgs")

    for path in ("/", "/repos", "/people", "/manage", "/orgs", "/reports", "/settings"):
        page = await client.get(path)
        assert page.status_code == 200, path
        assert "gh" in page.text

    trend = await client.get("/api/trend?range=30d")
    assert trend.status_code == 200
    assert trend.json()["labels"] == []

    insight = await client.get("/partials/insight?view=dashboard")
    assert insight.status_code == 200
    assert insight.text == ""

    scope = await client.get("/scope?orgs=1&next=/repos")
    assert scope.status_code == 303
    assert scope.headers["location"] == "/repos"

    second = await client.post(
        "/orgs/add", data={"login": "acme", "token": "github_pat_x"}
    )
    assert second.status_code == 303
    assert "already" in second.headers["location"]

    recipients = await client.post(
        "/settings/recipients/add", data={"email": "boss@example.com"}
    )
    assert recipients.status_code == 303
    settings_page = await client.get("/settings")
    assert "boss@example.com" in settings_page.text

    schedules = await client.post(
        "/reports/schedules", data={"sched-month-overview": "on"}
    )
    assert schedules.status_code == 303
    reports_page = await client.get("/reports")
    assert (
        'name="sched-month-overview"\n                   checked'
        in reports_page.text.replace("\r", "")
        or "checked" in reports_page.text
    )
