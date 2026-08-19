"""Record genuine OpenWeather MCP responses as replay fixtures.

Calls the live `weather` tool once per demo settlement and saves the verbatim
text to fixtures/openweather/<slug>.txt. Also records one genuine error
response (unknown city) to <slug>.error.txt so the failure path can be
replayed offline. Requires OWM_API_KEY in .env.

Run: python scripts/fetch_fixtures.py
"""
from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters, stdio_client

REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / ".env")

FIXTURES_DIR = REPO_ROOT / "fixtures" / "openweather"
CITIES = ["Vorokhta,UA", "Yasinia,UA", "Rakhiv,UA", "Verkhovyna,UA"]
INVALID_CITY = "Nowhereville-Xyz"


def slug(city: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", city.lower()).strip("_")


async def main() -> None:
    if not os.environ.get("OWM_API_KEY"):
        raise SystemExit("OWM_API_KEY is not set; fixtures must be genuine responses.")
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    weather_bin = os.environ.get("OPENWEATHER_MCP_BIN") or str(
        REPO_ROOT / "bin" / "mcp-openweather.exe"
    )
    params = StdioServerParameters(
        command=weather_bin, args=[], env={"OWM_API_KEY": os.environ["OWM_API_KEY"]}
    )
    async with Client(stdio_client(params)) as client:
        for city in CITIES:
            result = await client.call_tool(
                "weather", {"city": city, "units": "c", "lang": "en"}
            )
            text = "".join(getattr(b, "text", "") for b in result.content)
            if result.is_error:
                print(f"WARNING: {city} returned an error, not saving: {text}")
                continue
            path = FIXTURES_DIR / f"{slug(city)}.txt"
            path.write_text(text, encoding="utf-8")
            print(f"Saved {path.name} ({len(text)} chars)")

        result = await client.call_tool(
            "weather", {"city": INVALID_CITY, "units": "c", "lang": "en"}
        )
        text = "".join(getattr(b, "text", "") for b in result.content)
        # Observed behavior: this server version swallows API errors and
        # returns a degenerate all-zeros text with is_error=False. Save it
        # exactly as observed so replay is faithful; only a genuine MCP error
        # is stored as an .error.txt fixture.
        suffix = ".error.txt" if result.is_error else ".txt"
        path = FIXTURES_DIR / f"{slug(INVALID_CITY)}{suffix}"
        path.write_text(text, encoding="utf-8")
        print(f"Saved invalid-city fixture {path.name} (is_error={result.is_error}): {text[:80]!r}")


if __name__ == "__main__":
    asyncio.run(main())
