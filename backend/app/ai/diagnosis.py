import json
from typing import Any

from loguru import logger

from app.ai.llm_client import LLMClient
from app.ai.prompt_builder import build_messages
from app.core.config import settings


class DiagnosisService:
    def __init__(self) -> None:
        self.client = LLMClient(
            settings.openrouter_api_key,
            settings.openrouter_model,
            settings.llm_api_base_url,
        )

    async def diagnose(self, investigation: dict[str, Any]) -> dict[str, Any]:
        try:
            content = await self.client.complete(build_messages(investigation))
            parsed = self._parse_response(content)
            return {"available": True, **parsed}
        except Exception as exc:
            logger.error("Diagnosis failed: {}", exc)
            return {"available": False, "error": str(exc)}

    @staticmethod
    def _parse_response(content: str) -> dict[str, Any]:
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        data = json.loads(cleaned)
        required = ["root_cause", "explanation", "suggested_fix", "kubectl_commands", "prevention_recommendation", "confidence"]
        if any(key not in data for key in required):
            raise ValueError("LLM response is missing required diagnosis fields")
        data["confidence"] = max(0, min(100, int(data["confidence"])))
        if not isinstance(data["kubectl_commands"], list):
            raise ValueError("kubectl_commands must be an array")
        return {key: data[key] for key in required}
