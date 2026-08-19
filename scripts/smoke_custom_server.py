"""Smoke test: start trailsmith-mcp as a separate stdio process, list tools,
call one tool successfully and one with an invalid input.

Run: python scripts/smoke_custom_server.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters, stdio_client

REPO_ROOT = Path(__file__).resolve().parents[1]


async def main() -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "trailsmith_mcp"],
        cwd=str(REPO_ROOT),
    )
    async with Client(stdio_client(params)) as client:
        tools = await client.list_tools()
        print(f"Discovered {len(tools.tools)} tools:")
        for tool in tools.tools:
            print(f"  - {tool.name}")

        result = await client.call_tool("validate_itinerary", {
            "itinerary": {"days": [
                {"date": "2026-09-12", "segments": ["CH-005", "CH-004"]},
            ]},
            "party": {"fitness": "moderate", "size": 4, "has_tent": True},
        })
        print("\nvalidate_itinerary (valid input):")
        print(json.dumps(result.structured_content, indent=2))

        bad = await client.call_tool("assess_segment_risk", {
            "segments": ["CH-999"],
            "weather": {"temp_min_c": 5, "temp_max_c": 15, "precip_mm": 0,
                        "wind_ms": 3, "thunderstorm": False},
        })
        print("\nassess_segment_risk (unknown segment) -> is_error =", bad.is_error)
        print(bad.content[0].text if bad.content else "<no content>")


if __name__ == "__main__":
    asyncio.run(main())
