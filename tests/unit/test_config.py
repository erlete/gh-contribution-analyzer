import httpx2

from gca.config import Settings
from gca.main import create_app


def test_settings_defaults() -> None:
    s = Settings(_env_file=None)
    assert s.tz == "UTC"
    assert s.smtp_port == 587
    assert s.smtp_starttls is True


def test_settings_env_override(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("APP_SECRET_KEY", "abc")
    monkeypatch.setenv("SMTP_PORT", "1025")
    s = Settings(_env_file=None)
    assert s.app_secret_key == "abc"
    assert s.smtp_port == 1025


async def test_healthz() -> None:
    app = create_app()
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        resp = await client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"]
