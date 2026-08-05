"""Cached AI insights for dashboard views.

Insight prose is generated at most once per (view, scope, period, data)
combination: a stable hash of those inputs is the cache key and the
`insights` table stores the generated text. On a cache miss the configured
endpoint is called; when AI is unconfigured or the call fails the caller
gets `None` and the view simply renders without prose.
"""

import hashlib
import json
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from gca.ai.client import AIClient
from gca.models import Insight
from gca.services.settings import SettingsStore

_SYSTEM_PROMPT = (
    "You are an engineering analytics assistant for a GitHub contribution "
    "dashboard. Write short, factual, plain-prose insights in English. "
    "No markdown, no headers, no bullet lists, at most 120 words. Mention "
    "concrete numbers from the data. Do not invent data."
)


def cache_key(
    view: str, scope_key: str, period_key: str, context: dict[str, object]
) -> str:
    payload = (
        f"{view}|{scope_key}|{period_key}|"
        f"{json.dumps(context, sort_keys=True, default=str)}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:64]


async def insight_for(
    session: AsyncSession,
    *,
    view: str,
    scope_key: str,
    period_key: str,
    context: dict[str, object],
) -> str | None:
    key = cache_key(view, scope_key, period_key, context)
    cached = await session.scalar(sa.select(Insight).where(Insight.cache_key == key))
    if cached is not None:
        return cached.content
    config = await SettingsStore(session).ai_config()
    if config is None:
        return None
    user = (
        f"View: {view}\n"
        f"Period: {period_key}\n"
        f"Data: {json.dumps(context, sort_keys=True, default=str)}\n"
        "Summarize the notable trends and outliers."
    )
    try:
        async with AIClient(config) as client:
            content = await client.complete(_SYSTEM_PROMPT, user)
    except Exception:
        return None
    session.add(Insight(cache_key=key, view=view, content=content, model=config.model))
    await session.flush()
    return content


async def invalidate_view(session: AsyncSession, view: str) -> int:
    result: sa.CursorResult[Any] = await session.execute(  # type: ignore[assignment]
        sa.delete(Insight).where(Insight.view == view)
    )
    return int(result.rowcount or 0)
