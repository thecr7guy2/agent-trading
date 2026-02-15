import json
import logging
import re
from typing import TypeVar

from anthropic import AsyncAnthropic
from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

JSON_INSTRUCTION = (
    "\n\nYou MUST respond with valid JSON only. No markdown, no code fences, "
    "no explanation outside the JSON object."
)


def _extract_json(text: str) -> str:
    # Strip markdown code fences if present
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        return match.group(1).strip()
    # Try to find a JSON object directly
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        return match.group(0)
    return text.strip()


class ClaudeProvider:
    def __init__(self, api_key: str):
        self._client = AsyncAnthropic(api_key=api_key)

    async def generate(
        self,
        model: str,
        system_prompt: str,
        user_message: str,
        output_model: type[T],
        max_tokens: int = 4096,
    ) -> T:
        full_system = system_prompt + JSON_INSTRUCTION
        response = await self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=full_system,
            messages=[{"role": "user", "content": user_message}],
        )

        raw_text = response.content[0].text
        cleaned = _extract_json(raw_text)

        try:
            return output_model.model_validate_json(cleaned)
        except Exception:
            logger.warning("Direct JSON parse failed, attempting relaxed parse")
            # Try parsing as dict first to handle type coercion
            data = json.loads(cleaned)
            return output_model.model_validate(data)
