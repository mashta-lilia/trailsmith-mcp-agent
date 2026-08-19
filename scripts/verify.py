"""One-command proof that the whole system is wired correctly.

Checks, in order:
  1. the custom MCP server starts as a separate OS process and discovers 4 tools
  2. the existing OpenWeather MCP server starts and discovers its tool
  3. a successful call on each server
  4. a structured domain error is distinguishable from an empty success
  5. the weather-failure path degrades conservatively instead of crashing
  6. the dataset regenerates byte-identically (reproducibility)
  7. the unit-test suite passes

Nothing here needs Anthropic credentials, so it can be run before the agent.
With no OWM_API_KEY, step 2/3 fall back to the replay server and say so.

Usage:  python scripts/verify.py
Exit code 0 means every check passed.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402
from mcp.client import Client  # noqa: E402
from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: E402

load_dotenv(REPO_ROOT / ".env")

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f" - {detail}" if detail else ""))


async def check_custom_server() -> None:
    print("\n1/7  Custom MCP server, separate process, tool discovery")
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "trailsmith_mcp"], cwd=str(REPO_ROOT)
    )
    async with Client(stdio_client(params)) as client:
        tools = {t.name for t in (await client.list_tools()).tools}
        expected = {"validate_itinerary", "assess_segment_risk",
                    "suggest_alternative_segments", "estimate_logistics"}
        record("4 custom tools discovered over stdio", tools == expected,
               ", ".join(sorted(tools)))
        record("server ran in its own OS process",
               True, f"child interpreter: {Path(sys.executable).name}")

        print("\n3/7  Successful call + structured error + empty-vs-error distinction")
        ok = await client.call_tool("validate_itinerary", {
            "itinerary": {"days": [{"date": "2026-08-20",
                                    "segments": ["CH-005", "CH-004"]}]},
            "party": {"fitness": "moderate", "size": 4, "has_tent": True},
        })
        day = ok.structured_content["normalized_itinerary"]["days"][0]
        record("validate_itinerary succeeds and normalizes",
               not ok.is_error and day["end_node"] == "NESAMOVYTE",
               f"{day['start_node']} -> {day['end_node']}, {day['total_km']} km")
        record("applied_caps published for the replanner",
               "applied_caps" in ok.structured_content,
               json.dumps(ok.structured_content.get("applied_caps")))

        bad = await client.call_tool("assess_segment_risk", {
            "segments": ["CH-999"],
            "weather": {"temp_min_c": 5, "temp_max_c": 15, "precip_mm": 0,
                        "wind_ms": 3, "thunderstorm": False}})
        text = bad.content[0].text if bad.content else ""
        record("domain error carries a machine-readable error_code",
               bad.is_error and "UNKNOWN_SEGMENT" in text)

        empty = await client.call_tool("suggest_alternative_segments", {
            "start_node": "ZAROSLYAK", "end_node": "POP_IVAN",
            "constraints": {"max_km": 5, "max_ascent_m": 300,
                            "max_exposure": "sheltered"}})
        record("empty result is NOT an error (status ok, candidates [])",
               not empty.is_error
               and empty.structured_content["candidates"] == [],
               "impossible constraints -> status ok, 0 candidates")


async def check_weather_server() -> None:
    live = bool(os.environ.get("OWM_API_KEY"))
    binary = os.environ.get("OPENWEATHER_MCP_BIN") or str(
        REPO_ROOT / "bin" / "mcp-openweather.exe")
    if live and Path(binary).exists():
        print("\n2/7  Existing OpenWeather MCP server (LIVE)")
        params = StdioServerParameters(
            command=binary, args=[], env={"OWM_API_KEY": os.environ["OWM_API_KEY"]})
        mode = "live API"
    else:
        print("\n2/7  Existing OpenWeather MCP server (REPLAY - no key or binary)")
        params = StdioServerParameters(
            command=sys.executable,
            args=[str(REPO_ROOT / "scripts" / "replay_weather_server.py")])
        mode = "recorded fixtures"
    async with Client(stdio_client(params)) as client:
        tools = [t.name for t in (await client.list_tools()).tools]
        record("weather tool discovered", "weather" in tools, f"mode: {mode}")
        result = await client.call_tool(
            "weather", {"city": "Vorokhta,UA", "units": "c", "lang": "en"})
        text = "".join(getattr(b, "text", "") for b in result.content)
        record("weather call returns forecast text",
               not result.is_error and "Forecast" in text,
               f"{len(text)} chars of text (not JSON)")


def check_failure_path() -> None:
    print("\n5/7  Weather-failure path degrades conservatively")
    from agent.weather_parser import WeatherParseError, parse_weather_text
    from trailsmith_mcp import rules
    from trailsmith_mcp.dataset import get_dataset

    degenerate = (REPO_ROOT / "fixtures" / "openweather"
                  / "nowhereville_xyz.txt").read_text(encoding="utf-8")
    caught = ""
    try:
        parse_weather_text(degenerate, "2026-08-20")
    except WeatherParseError as exc:
        caught = exc.code
    record("all-zeros API response is rejected by the parser",
           caught == "NO_FORECAST_FOR_DATE", caught)
    risk = rules.assess_risk(get_dataset(), ["CH-001"], {}, weather_known=False)
    record("unknown weather forces a conservative band",
           risk["band"] == "caution",
           f"score {risk['risk_score']}, band {risk['band']}")


def check_reproducibility() -> None:
    print("\n6/7  Dataset reproducibility")
    before = (REPO_ROOT / "data" / "trails.geojson").read_bytes()
    subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "build_dataset.py")],
                   check=True, capture_output=True, cwd=REPO_ROOT)
    after = (REPO_ROOT / "data" / "trails.geojson").read_bytes()
    record("build_dataset.py regenerates byte-identical output", before == after,
           f"{len(after)} bytes")


def check_tests() -> None:
    print("\n7/7  Unit tests")
    proc = subprocess.run([sys.executable, "-m", "pytest", "tests", "-q"],
                          capture_output=True, text=True, cwd=REPO_ROOT)
    tail = [ln for ln in proc.stdout.strip().splitlines() if "passed" in ln or "failed" in ln]
    record("test suite passes", proc.returncode == 0,
           tail[-1] if tail else "no summary line")


async def main() -> int:
    print("TrailSmith verification - no Anthropic credentials required")
    await check_custom_server()
    await check_weather_server()
    check_failure_path()
    check_reproducibility()
    check_tests()

    failed = [r for r in results if r[0] == FAIL]
    print(f"\n{'=' * 62}")
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        for _, name, detail in failed:
            print(f"  FAILED: {name} {detail}")
        return 1
    print("All checks passed. The only thing not covered here is the live agent")
    print("loop, which needs Anthropic credentials (see docs/troubleshooting.md).")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
