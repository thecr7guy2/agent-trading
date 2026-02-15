import json
import logging
import re
from typing import TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

JSON_INSTRUCTION = (
    "\n\nYou MUST respond with valid JSON only. No markdown, no code fences, "
    "no explanation outside the JSON object."
)


def _extract_json(text: str) -> str:
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        return match.group(1).strip()
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        return match.group(0)
    return text.strip()


class MiniMaxProvider:
    def __init__(self, api_key: str, base_url: str):
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def generate(
        self,
        model: str,
        system_prompt: str,
        user_message: str,
        output_model: type[T],
        max_tokens: int = 4096,
    ) -> T:
        full_system = system_prompt + JSON_INSTRUCTION
        response = await self._client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": full_system},
                {"role": "user", "content": user_message},
            ],
        )

        raw_text = response.choices[0].message.content
        cleaned = _extract_json(raw_text)

        try:
            return output_model.model_validate_json(cleaned)
        except Exception:
            logger.warning("Direct JSON parse failed, attempting relaxed parse")
            data = json.loads(cleaned)
            return output_model.model_validate(data)
