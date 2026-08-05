import httpx2

from gca.config import Settings
from gca.main import create_app


def test_settings_defaults() -> None:
    s = Settings(_env_file=None)
    assert s.tz == "UTC"
    assert s.clone_dir == "/data/clones"
    assert s.reports_dir == "/data/reports"


def test_settings_env_override(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("APP_SECRET_KEY", "abc")
    monkeypatch.setenv("TZ", "Europe/Madrid")
    s = Settings(_env_file=None)
    assert s.app_secret_key == "abc"
    assert s.tz == "Europe/Madrid"


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
