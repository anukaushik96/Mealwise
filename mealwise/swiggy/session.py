"""A connected MCP session against one Swiggy server, with envelope unwrapping."""

from __future__ import annotations

import contextlib
import json
from types import TracebackType
from typing import Any, Literal

from mcp import ClientSession
from mcp.client.streamable_http import create_mcp_http_client

# The transport factory was renamed in mcp 2.0 (`streamablehttp_client` ->
# `streamable_http_client`). Accept either so the client works on both major versions.
try:
    from mcp.client.streamable_http import streamable_http_client
except ImportError:  # mcp < 2.0
    from mcp.client.streamable_http import (  # type: ignore[attr-defined,no-redef]
        streamablehttp_client as streamable_http_client,
    )

from .envelope import SwiggyToolError, unwrap

__all__ = ["SwiggyServer", "SwiggySession", "ENDPOINT"]

SwiggyServer = Literal["food", "instamart"]

# Endpoints per docs/reference/{food,instamart}/index.md.
# Note Instamart is `/im`, not `/instamart`.
ENDPOINT: dict[str, str] = {
    "food": "https://mcp.swiggy.com/food",
    "instamart": "https://mcp.swiggy.com/im",
}


class SwiggySession:
    """One session per server.

    Food and Instamart are separate MCP endpoints and do not share a cart, so ordering
    from both means two sessions. Use as an async context manager::

        async with SwiggySession.connect("instamart", token) as session:
            ...
    """

    def __init__(self, server: SwiggyServer, access_token: str) -> None:
        self.server = server
        self._access_token = access_token
        self._stack: contextlib.AsyncExitStack | None = None
        self._session: ClientSession | None = None

    @classmethod
    def connect(cls, server: SwiggyServer, access_token: str) -> SwiggySession:
        """Build an unconnected session; enter it as a context manager to connect."""
        return cls(server, access_token)

    async def __aenter__(self) -> SwiggySession:
        stack = contextlib.AsyncExitStack()
        await stack.__aenter__()
        try:
            # Session credentials ride on the HTTP header. You never pass user identity
            # or tokens as tool arguments.
            http_client = create_mcp_http_client(
                headers={"Authorization": f"Bearer {self._access_token}"}
            )
            await stack.enter_async_context(http_client)
            read_stream, write_stream = await stack.enter_async_context(
                streamable_http_client(ENDPOINT[self.server], http_client=http_client)
            )
            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            await session.initialize()
        except BaseException:
            await stack.aclose()
            raise
        self._stack = stack
        self._session = session
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self._stack = None
        self._session = None

    async def call(self, tool: str, args: dict[str, Any] | None = None) -> Any:
        """Call a tool and return its unwrapped payload, raising on ``success: False``."""
        return unwrap(tool, await self.call_raw(tool, args))

    async def call_raw(self, tool: str, args: dict[str, Any] | None = None) -> Any:
        """Call a tool and return the parsed response WITHOUT raising on failure.

        Needed for ``check_payment_status``, where a terminal payment *failure* is still
        a successful status read and the real outcome lives on the payload's ``status``.
        """
        if self._session is None:
            raise RuntimeError("session is not connected — use `async with`")
        result = await self._session.call_tool(tool, args or {})
        return self._parse(tool, result)

    async def list_tools(self) -> list[Any]:
        """Live ``input_schema`` for every tool this server exposes -- authoritative."""
        if self._session is None:
            raise RuntimeError("session is not connected — use `async with`")
        return (await self._session.list_tools()).tools

    @staticmethod
    def _parse(tool: str, result: Any) -> Any:
        """MCP returns content blocks; prefer ``structured_content``.

        The live server's text block is human-readable PROSE ("Found 23 saved
        addresses..."), not JSON -- so it is only a fallback, and a parse failure there
        is not fatal on its own.
        """
        structured = getattr(result, "structured_content", None)
        if structured is not None:
            return structured

        text = "".join(
            block.text
            for block in (getattr(result, "content", None) or [])
            if getattr(block, "type", None) == "text" and isinstance(getattr(block, "text", None), str)
        )
        if not text:
            raise SwiggyToolError(tool, "empty response — no text content block")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            raise SwiggyToolError(tool, f"response was not JSON: {text[:200]}") from None
