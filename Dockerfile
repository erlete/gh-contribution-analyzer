# Multi-stage build following Astral's official uv Docker guidance.

FROM python:3.14-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.1 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM python:3.14-slim
LABEL org.opencontainers.image.source="https://github.com/erlete/gh-contribution-analyzer" \
      org.opencontainers.image.description="Multi-org GitHub contribution analyzer" \
      org.opencontainers.image.licenses="MIT"
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz-subset0 \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 999 gca \
    && useradd --system --uid 999 --gid gca --create-home gca \
    && mkdir -p /data/clones /data/reports \
    && chown -R gca:gca /data
COPY --from=builder --chown=gca:gca /app/.venv /app/.venv
COPY --chown=gca:gca docker/gca-askpass docker/entrypoint.sh /usr/local/bin/
COPY --chown=gca:gca migrations /app/migrations
COPY --chown=gca:gca alembic.ini /app/alembic.ini
RUN chmod 755 /usr/local/bin/gca-askpass /usr/local/bin/entrypoint.sh
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    GIT_ASKPASS=/usr/local/bin/gca-askpass
WORKDIR /app
USER gca
ENTRYPOINT ["entrypoint.sh"]
CMD ["uvicorn", "gca.main:app", "--host", "0.0.0.0", "--port", "8000"]
