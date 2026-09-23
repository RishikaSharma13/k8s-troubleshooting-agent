from typing import Any

import httpx
from loguru import logger


class LLMClient:
    def __init__(self, api_key: str, model: str, base_url: str, timeout: float = 45.0) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def complete(self, messages: list[dict[str, str]]) -> str:
        if not self.api_key:
            raise RuntimeError("LLM API key is not configured")
        if not self.model:
            raise RuntimeError("LLM model is not configured")
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 1000,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        last_error: Exception | None = None
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(3):
                try:
                    response = await client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
                    response.raise_for_status()
                    body = response.json()
                    message = body["choices"][0]["message"]
                    content = message.get("content")
                    if isinstance(content, list):
                        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
                    if not isinstance(content, str) or not content.strip():
                        raise RuntimeError("LLM returned no usable text content; try a different model")
                    return content
                except (httpx.HTTPError, KeyError, IndexError, ValueError, RuntimeError) as exc:
                    last_error = exc
                    logger.warning("LLM request failed on attempt {}: {}", attempt + 1, exc)
        raise RuntimeError(f"LLM request failed after 3 attempts: {last_error}")
