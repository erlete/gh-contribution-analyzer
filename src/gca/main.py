"""FastAPI application factory and setup gate."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from gca import __version__
from gca.config import get_settings
from gca.db.engine import get_session_factory
from gca.services.orgs import has_validated_org
from gca.services.settings import SettingsStore
from gca.web.routers import (
    dashboard,
    manage,
    misc,
    orgs,
    people,
    reports,
    repos,
    setup,
)
from gca.web.routers import (
    settings as settings_router,
)


def _is_open_path(path: str) -> bool:
    return path in ("/healthz", "/setup") or path.startswith("/static/")


_UNSAFE_METHODS = ("POST", "PUT", "PATCH", "DELETE")


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    factory = get_session_factory()
    async with factory() as session:
        store = SettingsStore(session)
        await store.seed_from_env(get_settings())
        await session.commit()
    Path(get_settings().reports_dir).mkdir(parents=True, exist_ok=True)
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="gh-contribution-analyzer", version=__version__, lifespan=_lifespan
    )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.middleware("http")
    async def origin_guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Reject cross-site state-changing requests.

        Auth is HTTP basic at the Caddy edge, which browsers replay on
        cross-site form posts, so same-origin is enforced here instead of
        with per-form tokens. Non-browser clients send no Origin/Referer and
        pass through.
        """
        if request.method in _UNSAFE_METHODS:
            source = request.headers.get("origin") or request.headers.get("referer")
            if source:
                host = urlsplit(source).netloc.rsplit("@", 1)[-1]
                expected = request.headers.get("host", "")
                if host and expected and host != expected:
                    return PlainTextResponse(
                        "cross-origin request rejected", status_code=403
                    )
        return await call_next(request)

    @app.middleware("http")
    async def setup_gate(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if _is_open_path(request.url.path):
            return await call_next(request)
        factory = get_session_factory()
        async with factory() as session:
            ready = await has_validated_org(session)
        if not ready:
            return RedirectResponse("/setup", status_code=303)
        return await call_next(request)

    static_dir = Path(__file__).parent / "web" / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.include_router(setup.router)
    app.include_router(misc.router)
    app.include_router(dashboard.router)
    app.include_router(repos.router)
    app.include_router(people.router)
    app.include_router(manage.router)
    app.include_router(orgs.router)
    app.include_router(reports.router)
    app.include_router(settings_router.router)
    return app


app = create_app()
