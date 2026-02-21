from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, TypeVar

from openai import APIConnectionError, APITimeoutError, AsyncOpenAI, InternalServerError
from pydantic import BaseModel, ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

if TYPE_CHECKING:
    from src.agents.tool_executor import ToolExecutor

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

JSON_INSTRUCTION = (
    "\n\nYou MUST respond with valid JSON only. No markdown, no code fences, "
    "no explanation outside the JSON object. "
    'NEVER use null values — use "" for strings, 0 for numbers, and [] for arrays. '
    "Keep summaries short (1 sentence max) to stay within output limits."
)


def _extract_json(text: str) -> str:
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        return match.group(1).strip()
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        return match.group(0)
    return text.strip()


def _repair_json(text: str) -> str:
    text = text.strip()
    # Count unclosed braces/brackets and close them
    open_braces = text.count("{") - text.count("}")
    open_brackets = text.count("[") - text.count("]")
    # Remove trailing comma before closing
    text = re.sub(r",\s*$", "", text)
    text += "]" * open_brackets + "}" * open_braces
    return text


class MiniMaxProvider:
    def __init__(self, api_key: str, base_url: str):
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=120.0)

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
        max_tokens: int = 18192,
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
            try:
                data = json.loads(cleaned)
            except json.JSONDecodeError:
                logger.warning("JSON parse failed, attempting repair")
                repaired = _repair_json(cleaned)
                data = json.loads(repaired)
            return output_model.model_validate(data)

    async def generate_with_tools(
        self,
        model: str,
        system_prompt: str,
        user_message: str,
        output_model: type[T],
        tools: list[dict],
        tool_executor: ToolExecutor,
        max_tool_rounds: int = 15,
        max_tokens: int = 18192,
    ) -> tuple[T, int]:
        full_system = system_prompt + JSON_INSTRUCTION
        messages: list[dict] = [
            {"role": "system", "content": full_system},
            {"role": "user", "content": user_message},
        ]
        total_tool_calls = 0

        for round_num in range(max_tool_rounds):
            response = await self._client.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                messages=messages,
                tools=tools,
            )

            choice = response.choices[0]
            message = choice.message

            if not message.tool_calls:
                # No tool calls — extract final JSON from content
                raw_text = message.content or ""
                if not raw_text.strip():
                    raise ValueError("MiniMax returned empty text with no tool calls")
                return self._parse_output(raw_text, output_model), total_tool_calls

            # Execute all tool calls in parallel
            logger.info(
                "Tool round %d: %d calls — %s",
                round_num + 1,
                len(message.tool_calls),
                ", ".join(
                    f"{tc.function.name}({tc.function.arguments})" for tc in message.tool_calls
                ),
            )
            total_tool_calls += len(message.tool_calls)

            batch_calls = []
            for tc in message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}
                batch_calls.append((tc.function.name, args))

            results = await tool_executor.execute_batch(batch_calls)

            # Append assistant message (with tool_calls)
            messages.append(
                {
                    "role": "assistant",
                    "content": message.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in message.tool_calls
                    ],
                }
            )

            # Append tool results
            for tc, result in zip(message.tool_calls, results):
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result, default=str),
                    }
                )

        # Max rounds hit — force a final response without tools
        logger.warning("Max tool rounds (%d) reached, forcing final response", max_tool_rounds)
        messages.append(
            {
                "role": "user",
                "content": (
                    "You have used all available tool rounds. "
                    "Respond NOW with your final JSON output based on the data collected so far."
                ),
            }
        )
        response = await self._client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=messages,
        )
        raw_text = response.choices[0].message.content or ""
        if not raw_text.strip():
            raise ValueError("MiniMax returned empty text after max tool rounds")
        return self._parse_output(raw_text, output_model), total_tool_calls

    def _parse_output(self, raw_text: str, output_model: type[T]) -> T:
        cleaned = _extract_json(raw_text)
        try:
            return output_model.model_validate_json(cleaned)
        except (ValidationError, json.JSONDecodeError):
            logger.warning("Direct JSON parse failed, attempting relaxed parse")
            try:
                data = json.loads(cleaned)
            except json.JSONDecodeError:
                logger.warning("JSON parse failed, attempting repair")
                repaired = _repair_json(cleaned)
                data = json.loads(repaired)
            return output_model.model_validate(data)
