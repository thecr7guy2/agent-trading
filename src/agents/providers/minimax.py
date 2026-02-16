import json
import logging
import re
from typing import TypeVar

from openai import APIConnectionError, APITimeoutError, AsyncOpenAI, InternalServerError
from pydantic import BaseModel, ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

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

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((APIConnectionError, APITimeoutError, InternalServerError)),
        reraise=True,
        before_sleep=lambda rs: logger.warning(
            "MiniMax API retry #%d after %s", rs.attempt_number, rs.outcome.exception()
        ),
    )
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

        if not response.choices or not response.choices[0].message.content:
            raise ValueError("MiniMax returned empty response")
        raw_text = response.choices[0].message.content
        cleaned = _extract_json(raw_text)

        try:
            return output_model.model_validate_json(cleaned)
        except (ValidationError, json.JSONDecodeError):
            logger.warning("Direct JSON parse failed, attempting relaxed parse")
            data = json.loads(cleaned)
            return output_model.model_validate(data)
