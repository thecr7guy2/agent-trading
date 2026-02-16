import json
import logging
import re
from typing import TypeVar

from anthropic import APIConnectionError, APITimeoutError, AsyncAnthropic, InternalServerError
from pydantic import BaseModel, ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

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

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((APIConnectionError, APITimeoutError, InternalServerError)),
        reraise=True,
        before_sleep=lambda rs: logger.warning(
            "Claude API retry #%d after %s", rs.attempt_number, rs.outcome.exception()
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
        response = await self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=full_system,
            messages=[{"role": "user", "content": user_message}],
        )

        if not response.content:
            raise ValueError("Claude returned empty content array")
        raw_text = response.content[0].text
        cleaned = _extract_json(raw_text)

        try:
            return output_model.model_validate_json(cleaned)
        except (ValidationError, json.JSONDecodeError):
            logger.warning("Direct JSON parse failed, attempting relaxed parse")
            # Try parsing as dict first to handle type coercion
            data = json.loads(cleaned)
            return output_model.model_validate(data)
