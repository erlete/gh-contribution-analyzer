"""Insight generation with graceful AI fallback.

Every insight area always yields text. When an AI endpoint is configured the
prose comes from the model and is cached per (view, scope, period, data,
instructions, model) hash; when AI is unconfigured or the call fails, the
same context renders through deterministic fallback statements instead. The
`ai` flag on the result drives the AI chip in web views and reports.

Operator instructions are stored per area (dashboard, person, repo, report)
and appended to the prompt; because they are part of the cache key, editing
them regenerates affected insights naturally.
"""

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from gca.ai.client import AIClient
from gca.ai.fallback import fallback_text
from gca.models import Insight
from gca.services.settings import SettingsStore

log = logging.getLogger("gca.ai")

_SYSTEM_PROMPT = (
    "You are an engineering analytics assistant for a GitHub contribution "
    "dashboard. Write short, factual, plain-prose insights in English. "
    "No markdown, no headers, no bullet lists, at most 120 words. Mention "
    "concrete numbers from the data. Do not invent data."
)


@dataclass
class InsightResult:
    text: str
    ai: bool


def cache_key(
    view: str,
    scope_key: str,
    period_key: str,
    context: dict[str, object],
    extra: str = "",
) -> str:
    payload = (
        f"{view}|{scope_key}|{period_key}|"
        f"{json.dumps(context, sort_keys=True, default=str)}|{extra}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:64]


async def insight_for(
    session: AsyncSession,
    *,
    view: str,
    scope_key: str,
    period_key: str,
    context: dict[str, object],
    area: str = "dashboard",
    kind: str | None = None,
) -> InsightResult:
    """Return insight text for an area. `area` selects the operator
    instructions bucket; `kind` selects the fallback context shape and
    defaults to `area`."""
    store = SettingsStore(session)
    config = await store.ai_config()
    if config is not None:
        instructions = (await store.ai_instructions()).get(area, "")
        key = cache_key(
            view, scope_key, period_key, context, f"{instructions}|{config.model}"
        )
        cached = await session.scalar(
            sa.select(Insight).where(Insight.cache_key == key)
        )
        if cached is not None:
            return InsightResult(text=cached.content, ai=True)
        system = _SYSTEM_PROMPT
        if instructions.strip():
            system += (
                "\nOperator instructions for this area (follow them as long "
                f"as they do not contradict the data): {instructions.strip()}"
            )
        user = (
            f"View: {view}\n"
            f"Period: {period_key}\n"
            f"Data: {json.dumps(context, sort_keys=True, default=str)}\n"
            "Summarize the notable trends and outliers."
        )
        try:
            async with AIClient(config) as client:
                content = await client.complete(system, user)
        except Exception as exc:
            log.warning("ai insight generation failed for %s: %s", view, exc)
        else:
            session.add(
                Insight(cache_key=key, view=view, content=content, model=config.model)
            )
            await session.flush()
            return InsightResult(text=content, ai=True)
    return InsightResult(text=fallback_text(kind or area, context), ai=False)


async def invalidate_view(session: AsyncSession, view: str) -> int:
    result: sa.CursorResult[Any] = await session.execute(  # type: ignore[assignment]
        sa.delete(Insight).where(Insight.view == view)
    )
    return int(result.rowcount or 0)


# View-name shapes per instruction area. Changing an area's instructions
# already changes the cache key, so stale rows could never be served again;
# deleting them makes the regeneration explicit and prunes dead cache.
_AREA_VIEW_PREFIXES = {
    "dashboard": ("dashboard",),
    "person": ("person:",),
    "repo": ("repo:",),
    "report": ("report:",),
}


async def invalidate_area(session: AsyncSession, area: str) -> int:
    """Delete every cached insight belonging to an instruction area, so all
    affected AI comments regenerate as their views load."""
    deleted = 0
    for prefix in _AREA_VIEW_PREFIXES.get(area, ()):
        condition = (
            Insight.view == prefix
            if not prefix.endswith(":")
            else Insight.view.like(prefix + "%")
        )
        result: sa.CursorResult[Any] = await session.execute(  # type: ignore[assignment]
            sa.delete(Insight).where(condition)
        )
        deleted += int(result.rowcount or 0)
    return deleted
