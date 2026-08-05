"""Async client for the OpenAI-compatible chat completion endpoint.

The deployment targets a custom Qwen endpoint that speaks the standard
`/chat/completions` protocol. The client stays deliberately small: one POST,
a single retry on transient failure (5xx or timeout), and a strict error
envelope (`AIError`) so callers never deal with transport details.
"""

import asyncio

import httpx2

from gca.services.settings import AIConfig

_TIMEOUT = 30.0
_RETRY_SLEEP = 2.0


class AIError(RuntimeError):
    """The AI endpoint failed or returned an unusable response."""


class AIClient:
    def __init__(
        self,
        config: AIConfig,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        headers: dict[str, str] = {}
        if config.key:
            headers["Authorization"] = f"Bearer {config.key}"
        self._client = httpx2.AsyncClient(
            headers=headers,
            timeout=_TIMEOUT,
            transport=transport,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> AIClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def _post(self, url: str, body: dict[str, object]) -> httpx2.Response:
        """POST with one retry on 5xx or timeout, sleeping between attempts."""
        for attempt in range(2):
            try:
                response = await self._client.post(url, json=body)
            except httpx2.TimeoutException as exc:
                if attempt == 0:
                    await asyncio.sleep(_RETRY_SLEEP)
                    continue
                raise AIError("ai request timed out") from exc
            except httpx2.HTTPError as exc:
                raise AIError(f"ai request failed: {exc}") from exc
            if response.status_code >= 500 and attempt == 0:
                await asyncio.sleep(_RETRY_SLEEP)
                continue
            return response
        raise AIError("ai request failed after retry")

    async def complete(self, system: str, user: str, *, max_tokens: int = 700) -> str:
        url = self._config.url.rstrip("/") + "/chat/completions"
        body: dict[str, object] = {
            "model": self._config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.3,
        }
        response = await self._post(url, body)
        if response.status_code != 200:
            raise AIError(
                f"ai endpoint returned http {response.status_code}: "
                f"{response.text[:200]}"
            )
        try:
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise AIError(f"malformed ai response: {exc}") from exc
        if not isinstance(content, str) or not content.strip():
            raise AIError("ai response contained no content")
        return content.strip()
