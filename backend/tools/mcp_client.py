import asyncio
import json
import logging
import sys
import time
from contextlib import AsyncExitStack
from typing import Any

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    HAS_MCP = True
except ImportError:
    ClientSession = Any
    StdioServerParameters = Any
    stdio_client = Any
    HAS_MCP = False

logger = logging.getLogger(__name__)


class MCPClientError(Exception):
    """Base error for MCP client failures."""


class ToolNotFoundError(MCPClientError):
    """Raised when a requested tool is not in the discovered list."""


class ToolArgumentError(MCPClientError):
    """Raised when required tool arguments are missing."""


class ToolExecutionError(MCPClientError):
    """Raised when the server reports a tool execution error."""


class MCPConnectionError(MCPClientError):
    """Raised when the client is not connected or connection fails."""


class MCPClient:
    """Async stdio MCP client wrapper with discovery and validated calls."""

    def __init__(self, command: str, args: list[str] | None = None, env: dict[str, str] | None = None) -> None:
        """Store server launch parameters without connecting."""
        self._server_params = StdioServerParameters(command=command, args=args or [], env=env)
        self._exit_stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._tools_cache: list[dict] | None = None

    async def __aenter__(self) -> "MCPClient":
        """Connect the transport, enter the session, and initialize it."""
        stack = AsyncExitStack()
        try:
            await stack.__aenter__()
            read, write = await stack.enter_async_context(stdio_client(self._server_params))
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
        except Exception as exc:
            await stack.aclose()
            raise MCPConnectionError(f"Failed to connect to MCP server: {exc}") from exc
        self._exit_stack = stack
        self._session = session
        logger.info("Connected to MCP server command=%s", self._server_params.command)
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        """Close the session and transport stack."""
        if self._exit_stack is not None:
            await self._exit_stack.aclose()
        self._exit_stack = None
        self._session = None
        logger.info("Disconnected from MCP server")

    def _ensure_connected(self) -> ClientSession:
        """Return the active session or raise if not connected."""
        if self._session is None:
            raise MCPConnectionError("MCPClient is not connected; use 'async with MCPClient(...)'")
        return self._session

    async def list_tools(self, refresh: bool = False) -> list[dict]:
        """Discover server tools and cache their name, description, and schema."""
        session = self._ensure_connected()
        if self._tools_cache is not None and not refresh:
            return list(self._tools_cache)
        result = await session.list_tools()
        tools = [
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": dict(tool.inputSchema) if isinstance(tool.inputSchema, dict) else {},
            }
            for tool in result.tools
        ]
        self._tools_cache = tools
        logger.info("Discovered tools: %s", [t["name"] for t in tools])
        return list(tools)

    def has_tool(self, name: str) -> bool:
        """Check a tool name against the cached discovery list."""
        if self._tools_cache is None:
            return False
        return any(tool["name"] == name for tool in self._tools_cache)

    def _find_cached_tool(self, name: str) -> dict | None:
        """Find a cached tool spec by name."""
        if self._tools_cache is None:
            return None
        for tool in self._tools_cache:
            if tool["name"] == name:
                return tool
        return None

    @staticmethod
    def _extract_text(result: Any) -> str:
        """Join text content blocks from a tool result."""
        contents = getattr(result, "content", None) or []
        parts: list[str] = []
        for block in contents:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                parts.append(text)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "\n".join(parts)

    async def call_tool(self, name: str, arguments: dict | None = None, timeout: float = 30.0) -> Any:
        """Validate args, call the tool with a timeout, and return parsed content."""
        session = self._ensure_connected()
        if self._tools_cache is None:
            await self.list_tools()
        spec = self._find_cached_tool(name)
        if spec is None:
            raise ToolNotFoundError(f"Tool {name!r} not found")
        schema = spec.get("inputSchema") or {}
        required = schema.get("required") or []
        provided = arguments or {}
        missing = [key for key in required if key not in provided]
        if missing:
            raise ToolArgumentError(f"Missing required arguments for {name!r}: {missing}")
        start = time.perf_counter()
        try:
            result = await asyncio.wait_for(session.call_tool(name, provided), timeout)
        except TimeoutError as exc:
            duration = time.perf_counter() - start
            logger.error("Tool %s timed out after %.2fs", name, duration)
            raise ToolExecutionError(f"Tool {name!r} timed out after {timeout}s") from exc
        duration = time.perf_counter() - start
        logger.info("Called tool %s in %.3fs with args=%s", name, duration, provided)
        if getattr(result, "isError", False):
            raise ToolExecutionError(self._extract_text(result) or f"Tool {name!r} reported an error")
        structured = getattr(result, "structuredContent", None)
        if structured is not None:
            return structured
        text = self._extract_text(result)
        if not text:
            return text
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return text


async def main() -> None:
    """Connect, list tools, and call the first tool needing no arguments."""
    if len(sys.argv) < 2:
        logger.error("Usage: python mcp_client.py <command> [args...]")
        return
    command = sys.argv[1]
    args = sys.argv[2:] or []
    try:
        async with MCPClient(command, args) as client:
            tools = await client.list_tools()
            logger.info("Available tools: %s", [t["name"] for t in tools])
            target = None
            for tool in tools:
                required = (tool.get("inputSchema") or {}).get("required") or []
                if not required:
                    target = tool
                    break
            if target is None:
                logger.info("No zero-argument tool available")
                return
            logger.info("Calling tool %s", target["name"])
            result = await client.call_tool(target["name"], {})
            logger.info("Result from %s: %s", target["name"], result)
    except MCPClientError as exc:
        logger.error("MCP error: %s", exc)


if __name__ == "__main__":
    """Run the demo client against the server given on the command line."""
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())