import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from src.agents.providers.claude import ClaudeProvider, _extract_json
from src.agents.providers.minimax import MiniMaxProvider


class SimpleModel(BaseModel):
    name: str
    score: float


# --- _extract_json ---


class TestExtractJson:
    def test_plain_json(self):
        assert _extract_json('{"name": "test"}') == '{"name": "test"}'

    def test_code_fenced_json(self):
        text = '```json\n{"name": "test"}\n```'
        assert _extract_json(text) == '{"name": "test"}'

    def test_code_fenced_no_lang(self):
        text = '```\n{"name": "test"}\n```'
        assert _extract_json(text) == '{"name": "test"}'

    def test_json_with_surrounding_text(self):
        text = 'Here is the result: {"name": "test", "score": 1.0} hope that helps'
        result = _extract_json(text)
        parsed = json.loads(result)
        assert parsed["name"] == "test"

    def test_plain_text_passthrough(self):
        assert _extract_json("no json here") == "no json here"


# --- ClaudeProvider ---


class TestClaudeProvider:
    @pytest.mark.asyncio
    async def test_generate_parses_json(self):
        mock_client = AsyncMock()
        mock_content = MagicMock()
        mock_content.text = '{"name": "ASML", "score": 8.5}'
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        provider = ClaudeProvider(api_key="test-key")
        provider._client = mock_client

        result = await provider.generate(
            model="claude-haiku-4-5-20251001",
            system_prompt="You are a test agent.",
            user_message="Test input",
            output_model=SimpleModel,
        )

        assert result.name == "ASML"
        assert result.score == 8.5
        mock_client.messages.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_handles_code_fences(self):
        mock_client = AsyncMock()
        mock_content = MagicMock()
        mock_content.text = '```json\n{"name": "SAP", "score": 7.0}\n```'
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        provider = ClaudeProvider(api_key="test-key")
        provider._client = mock_client

        result = await provider.generate(
            model="claude-sonnet-4-5-20250929",
            system_prompt="Test",
            user_message="Test",
            output_model=SimpleModel,
        )

        assert result.name == "SAP"

    @pytest.mark.asyncio
    async def test_generate_relaxed_parse_fallback(self):
        mock_client = AsyncMock()
        mock_content = MagicMock()
        # Integer score instead of float — model_validate handles coercion
        mock_content.text = '{"name": "TEST", "score": 5}'
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        provider = ClaudeProvider(api_key="test-key")
        provider._client = mock_client

        result = await provider.generate(
            model="claude-sonnet-4-6",
            system_prompt="Test",
            user_message="Test",
            output_model=SimpleModel,
        )

        assert result.score == 5.0

    @pytest.mark.asyncio
    async def test_system_prompt_includes_json_instruction(self):
        mock_client = AsyncMock()
        mock_content = MagicMock()
        mock_content.text = '{"name": "X", "score": 1.0}'
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        provider = ClaudeProvider(api_key="test-key")
        provider._client = mock_client

        await provider.generate(
            model="claude-haiku-4-5-20251001",
            system_prompt="Base prompt.",
            user_message="Test",
            output_model=SimpleModel,
        )

        call_kwargs = mock_client.messages.create.call_args[1]
        system = call_kwargs["system"]
        # System is now a list of cached content blocks
        assert isinstance(system, list)
        assert "valid JSON only" in system[0]["text"]


# --- MiniMaxProvider ---


class TestMiniMaxProvider:
    @pytest.mark.asyncio
    async def test_generate_parses_json(self):
        mock_client = AsyncMock()
        mock_message = MagicMock()
        mock_message.content = '{"name": "RDSA", "score": 6.0}'
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        provider = MiniMaxProvider(api_key="test-key", base_url="https://api.minimaxi.chat/v1")
        provider._client = mock_client

        result = await provider.generate(
            model="MiniMax-Text-01",
            system_prompt="You are a test agent.",
            user_message="Test input",
            output_model=SimpleModel,
        )

        assert result.name == "RDSA"
        assert result.score == 6.0

    @pytest.mark.asyncio
    async def test_generate_handles_code_fences(self):
        mock_client = AsyncMock()
        mock_message = MagicMock()
        mock_message.content = '```json\n{"name": "BMW", "score": 7.5}\n```'
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        provider = MiniMaxProvider(api_key="test-key", base_url="https://example.com/v1")
        provider._client = mock_client

        result = await provider.generate(
            model="MiniMax-Text-01",
            system_prompt="Test",
            user_message="Test",
            output_model=SimpleModel,
        )

        assert result.name == "BMW"

    @pytest.mark.asyncio
    async def test_uses_system_message(self):
        mock_client = AsyncMock()
        mock_message = MagicMock()
        mock_message.content = '{"name": "X", "score": 1.0}'
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        provider = MiniMaxProvider(api_key="test-key", base_url="https://example.com/v1")
        provider._client = mock_client

        await provider.generate(
            model="MiniMax-Text-01",
            system_prompt="Base prompt.",
            user_message="Test",
            output_model=SimpleModel,
        )

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        messages = call_kwargs["messages"]
        assert messages[0]["role"] == "system"
        assert "valid JSON only" in messages[0]["content"]
        assert messages[1]["role"] == "user"
