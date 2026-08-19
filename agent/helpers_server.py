"""In-process SDK MCP server exposing the deterministic weather-text parser.

This is an agent-side implementation helper (it does not count toward the
custom server's three substantive tools). It exists so the day-assessor
subagent applies the exact same tested regex parser instead of eyeballing the
text, keeping the value trace deterministic.
"""
from __future__ import annotations

import json

from claude_agent_sdk import create_sdk_mcp_server, tool

from .weather_parser import WeatherParseError, parse_weather_text


@tool(
    "parse_weather_text",
    "Parse the raw text returned by the OpenWeather MCP `weather` tool into the "
    "structured weather summary required by trailsmith assess_segment_risk. "
    "Pass the full tool output and the target date (YYYY-MM-DD). On failure "
    "returns {\"status\": \"error\", \"code\": ...}; then treat the day's "
    "weather as unknown.",
    {"text": str, "target_date": str},
)
async def parse_weather_tool(args: dict) -> dict:
    try:
        summary = parse_weather_text(args["text"], args["target_date"])
        payload = {"status": "ok", "weather": summary.as_dict()}
    except WeatherParseError as exc:
        payload = {"status": "error", "code": exc.code, "message": str(exc)}
    return {"content": [{"type": "text", "text": json.dumps(payload)}]}


helpers_server = create_sdk_mcp_server(name="helpers", tools=[parse_weather_tool])
