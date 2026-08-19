"""Smoke test for the OpenWeather MCP server (existing server, Part A).

Starts the binary pointed to by OPENWEATHER_MCP_BIN (default: bin/mcp-openweather.exe),
lists tools, and calls `weather` for Vorokhta. With no OWM_API_KEY set this
demonstrates the missing-key failure mode.

Run: python scripts/smoke_weather_server.py [city]
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters, stdio_client

REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / ".env")

DEFAULT_BIN = str(REPO_ROOT / "bin" / "mcp-openweather.exe")


async def main() -> None:
    city = sys.argv[1] if len(sys.argv) > 1 else "Vorokhta,UA"
    env = {}
    if os.environ.get("OWM_API_KEY"):
        env["OWM_API_KEY"] = os.environ["OWM_API_KEY"]
    params = StdioServerParameters(
        command=os.environ.get("OPENWEATHER_MCP_BIN") or DEFAULT_BIN,
        args=[],
        env=env,
    )
    async with Client(stdio_client(params)) as client:
        tools = await client.list_tools()
        for tool in tools.tools:
            print(f"Tool: {tool.name}")
            print(f"Description: {tool.description}")
            print(f"Input schema: {tool.inputSchema if hasattr(tool, 'inputSchema') else tool.input_schema}")
        result = await client.call_tool("weather", {"city": city, "units": "c", "lang": "en"})
        print(f"\nweather({city!r}) -> is_error = {result.is_error}")
        for block in result.content:
            print(getattr(block, "text", block))


if __name__ == "__main__":
    asyncio.run(main())
