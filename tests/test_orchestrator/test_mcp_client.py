import pytest

from src.orchestrator.mcp_client import InProcessMCPClient


class TestInProcessMCPClient:
    @pytest.mark.asyncio
    async def test_call_tool_dispatches_correctly(self):
        async def greet(name: str) -> dict:
            return {"message": f"hello {name}"}

        client = InProcessMCPClient({"greet": greet})
        result = await client.call_tool("greet", {"name": "world"})
        assert result == {"message": "hello world"}

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        client = InProcessMCPClient({})
        result = await client.call_tool("nonexistent", {})
        assert "error" in result
        assert "Unknown tool" in result["error"]

    @pytest.mark.asyncio
    async def test_close_is_noop(self):
        client = InProcessMCPClient({})
        await client.close()
