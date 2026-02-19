from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import TYPE_CHECKING, TypeVar

from anthropic import APIConnectionError, APITimeoutError, AsyncAnthropic, InternalServerError
from pydantic import BaseModel, ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

if TYPE_CHECKING:
    from src.agents.tool_executor import ToolExecutor

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

JSON_INSTRUCTION = (
    "\n\nYou MUST respond with valid JSON only. No markdown, no code fences, "
    "no explanation outside the JSON object."
)

# Seconds to wait between tool rounds to avoid rate limits
_TOOL_ROUND_DELAY = 5


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


def _repair_json(text: str) -> str:
    text = text.strip()
    open_braces = text.count("{") - text.count("}")
    open_brackets = text.count("[") - text.count("]")
    text = re.sub(r",\s*$", "", text)
    text += "]" * open_brackets + "}" * open_braces
    return text


class ClaudeProvider:
    def __init__(self, api_key: str):
        # max_retries=5 gives 6 total attempts per API call.
        # The SDK handles 429 (rate limit) and 529 (overloaded) with
        # exponential backoff and Retry-After headers automatically.
        self._client = AsyncAnthropic(api_key=api_key, max_retries=5)

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
        max_tokens: int = 8192,
    ) -> tuple[T, int]:
        full_system = system_prompt + JSON_INSTRUCTION
        messages: list[dict] = [{"role": "user", "content": user_message}]
        total_tool_calls = 0

        for round_num in range(max_tool_rounds):
            # Throttle to avoid hitting Anthropic rate limits
            if round_num > 0:
                await asyncio.sleep(_TOOL_ROUND_DELAY)

            try:
                response = await self._client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    system=full_system,
                    messages=messages,
                    tools=tools,
                )
            except Exception:
                logger.exception(
                    "Claude API call failed at tool round %d (after SDK retries)",
                    round_num + 1,
                )
                # If we already gathered data via tool calls, try to
                # salvage a result by forcing a text-only response.
                if total_tool_calls > 0:
                    logger.info(
                        "Attempting to salvage result from %d tool calls collected so far",
                        total_tool_calls,
                    )
                    return (
                        await self._force_final(
                            model, max_tokens, full_system, messages, output_model
                        ),
                        total_tool_calls,
                    )
                raise

            # Separate tool_use blocks from text blocks
            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
            text_blocks = [b for b in response.content if b.type == "text"]

            if not tool_use_blocks:
                # No tool calls — extract final JSON from text
                raw_text = "".join(b.text for b in text_blocks)
                if not raw_text.strip():
                    raise ValueError("Claude returned empty text with no tool calls")
                return self._parse_output(raw_text, output_model), total_tool_calls

            # Execute all tool calls in parallel
            logger.info(
                "Tool round %d: %d calls — %s",
                round_num + 1,
                len(tool_use_blocks),
                ", ".join(f"{b.name}({b.input})" for b in tool_use_blocks),
            )
            total_tool_calls += len(tool_use_blocks)

            batch_calls = [(b.name, b.input) for b in tool_use_blocks]
            results = await tool_executor.execute_batch(batch_calls)

            # Build assistant message with all content blocks
            messages.append({"role": "assistant", "content": response.content})

            # Build tool result messages
            tool_results = []
            for block, result in zip(tool_use_blocks, results):
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, default=str),
                    }
                )
            messages.append({"role": "user", "content": tool_results})

        # Max rounds hit — force a final response without tools
        logger.warning("Max tool rounds (%d) reached, forcing final response", max_tool_rounds)
        return (
            await self._force_final(model, max_tokens, full_system, messages, output_model),
            total_tool_calls,
        )

    async def _force_final(
        self,
        model: str,
        max_tokens: int,
        system: str,
        messages: list[dict],
        output_model: type[T],
    ) -> T:
        """Ask Claude for a final JSON answer with no tools available."""
        messages = list(messages)  # shallow copy — don't mutate caller's list
        messages.append(
            {
                "role": "user",
                "content": (
                    "You have used all available tool rounds. "
                    "Respond NOW with your final JSON output based on the data collected so far."
                ),
            }
        )
        response = await self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
        )
        raw_text = "".join(b.text for b in response.content if b.type == "text")
        if not raw_text.strip():
            raise ValueError("Claude returned empty text after forcing final response")
        return self._parse_output(raw_text, output_model)

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
