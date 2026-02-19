from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.orchestrator.mcp_client import MCPToolClient

logger = logging.getLogger(__name__)


class ToolExecutor:
    def __init__(self, mcp_client: MCPToolClient, allowed_tools: set[str]):
        self._client = mcp_client
        self._allowed = allowed_tools

    async def execute(self, tool_name: str, args: dict[str, Any]) -> dict:
        if tool_name not in self._allowed:
            logger.warning("Tool call blocked — '%s' not in allowed set", tool_name)
            return {"error": f"Tool '{tool_name}' is not available"}
        try:
            result = await asyncio.wait_for(
                self._client.call_tool(tool_name, args),
                timeout=30.0,
            )
            result_len = len(json.dumps(result, default=str))
            logger.debug("Tool '%s'(%s) → %d bytes", tool_name, args, result_len)
            return result
        except TimeoutError:
            logger.warning("Tool '%s' timed out for args %s", tool_name, args)
            return {"error": f"Tool '{tool_name}' timed out"}
        except Exception as e:
            logger.exception("Tool '%s' failed", tool_name)
            return {"error": str(e)}

    async def execute_batch(self, calls: list[tuple[str, dict[str, Any]]]) -> list[dict]:
        tasks = [self.execute(name, args) for name, args in calls]
        return await asyncio.gather(*tasks)
