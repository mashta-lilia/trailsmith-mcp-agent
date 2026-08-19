"""Offline replay stand-in for the OpenWeather MCP server.

Exposes the same `weather` tool contract and serves the verbatim recorded
responses from fixtures/openweather/. The agent-side parsing and error
handling run completely unchanged: fixtures are raw tool text, never
pre-parsed answers. Cities recorded as <slug>.error.txt replay the genuine
error text as a tool error.

Started by the agent when REPLAY=1; can also be started manually:
python scripts/replay_weather_server.py
"""
from __future__ import annotations

import re
from pathlib import Path

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = REPO_ROOT / "fixtures" / "openweather"

server = MCPServer(name="weather")


def slug(city: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", city.lower()).strip("_")


@server.tool(
    name="weather",
    description="Get current and forecast weather information for a specific City",
)
def weather(city: str, units: str = "c", lang: str = "en") -> str:
    name = slug(city)
    error_path = FIXTURES_DIR / f"{name}.error.txt"
    if error_path.exists():
        raise ToolError(error_path.read_text(encoding="utf-8"))
    path = FIXTURES_DIR / f"{name}.txt"
    if not path.exists():
        raise ToolError(
            f"replay mode: no fixture recorded for city {city!r} "
            f"(run scripts/fetch_fixtures.py with a live OWM_API_KEY first)"
        )
    return path.read_text(encoding="utf-8")


if __name__ == "__main__":
    server.run("stdio")
