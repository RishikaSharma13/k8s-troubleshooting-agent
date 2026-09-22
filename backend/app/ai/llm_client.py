from typing import Any

import httpx
from loguru import logger


class LLMClient:
    def __init__(self, api_key: str, model: str, timeout: float = 45.0) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    async def complete(self, messages: list[dict[str, str]]) -> str:
        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not configured")
        if not self.model:
            raise RuntimeError("OPENROUTER_MODEL is not configured")
        payload: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": 0.1, "max_tokens": 1000}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        last_error: Exception | None = None
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(3):
                try:
                    response = await client.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload)
                    response.raise_for_status()
                    body = response.json()
                    return body["choices"][0]["message"]["content"]
                except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
                    last_error = exc
                    logger.warning("OpenRouter request failed on attempt {}: {}", attempt + 1, exc)
        raise RuntimeError(f"OpenRouter request failed after 3 attempts: {last_error}")
