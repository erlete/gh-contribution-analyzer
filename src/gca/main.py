"""FastAPI application factory and setup gate."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import RedirectResponse
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

_OPEN_PREFIXES = ("/static", "/healthz", "/setup")


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
    async def setup_gate(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if path.startswith(_OPEN_PREFIXES):
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
