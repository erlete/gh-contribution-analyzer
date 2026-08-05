"""FastAPI application factory."""

from fastapi import FastAPI

from gca import __version__


def create_app() -> FastAPI:
    app = FastAPI(title="gh-contribution-analyzer", version=__version__)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
