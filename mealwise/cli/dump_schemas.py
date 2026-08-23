"""Dump the LIVE ``input_schema`` for every tool on both Swiggy MCP servers.

The published docs leave argument shapes unspecified and under-report the tool count
(Instamart's index claims 16 against 19 pages; the server advertises 14). The MCP
``tools/list`` response carries the server's real JSON Schema, which is authoritative.

    SWIGGY_TOKEN=... python -m swiggy.cli.dump_schemas
    SWIGGY_TOKEN=... python -m swiggy.cli.dump_schemas update_cart
"""

from __future__ import annotations

import asyncio
import json

from ..swiggy.session import SwiggySession
from ._args import positional, require_token


async def dump(server: str, token: str, wanted: list[str]) -> None:
    async with SwiggySession.connect(server, token) as session:  # type: ignore[arg-type]
        tools = await session.list_tools()
        selected = [t for t in tools if t.name in wanted] if wanted else tools
        bar = "=" * 70
        print(f"\n{bar}\n{server.upper()} — {len(selected)}/{len(tools)} tools\n{bar}")
        for tool in selected:
            print(f"\n--- {tool.name} ---")
            print(json.dumps(tool.input_schema, indent=2))


async def main() -> None:
    token = require_token()
    wanted = positional()
    await dump("food", token, wanted)
    await dump("instamart", token, wanted)


if __name__ == "__main__":
    asyncio.run(main())
