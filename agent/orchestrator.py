"""Claude Agent SDK options: MCP wiring, subagent definitions, workflow prompt."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions

from .helpers_server import helpers_server

REPO_ROOT = Path(__file__).resolve().parents[1]

WEATHER_TOOL = "mcp__weather__weather"
PARSE_TOOL = "mcp__helpers__parse_weather_text"
VALIDATE_TOOL = "mcp__trailsmith__validate_itinerary"
RISK_TOOL = "mcp__trailsmith__assess_segment_risk"
ALTERNATIVES_TOOL = "mcp__trailsmith__suggest_alternative_segments"
LOGISTICS_TOOL = "mcp__trailsmith__estimate_logistics"

DAY_ASSESSOR_PROMPT = """You assess ONE hiking day. You will receive: a day number,
a date, an ordered list of segment IDs, and a settlement name for the forecast.

Steps, in order:
1. Call the weather tool with the settlement as `city`, units "c", lang "en".
2. Pass the FULL raw text output plus the target date to parse_weather_text.
3. If parsing returned status "ok", call assess_segment_risk with the day's
   segments and the parsed weather (weather_known=true). If the weather call
   failed or parsing returned an error, call assess_segment_risk with any
   syntactically valid weather values and weather_known=false instead.
4. Reply with EXACTLY one JSON object and nothing else:
   {"day": <n>, "date": "...", "settlement": "...",
    "weather_status": "ok"|"unavailable",
    "weather": <parsed weather object or null>,
    "raw_forecast_excerpt": "<the 1-3 lines of raw tool text that drove the "
    "most important risk factor, verbatim, or a note on what failed>",
    "risk_score": <int>, "band": "ok"|"caution"|"no_go",
    "factors": [...]}
Prefix every message you produce with [day-<n>] for log correlation.
Never invent forecast values: they must come from parse_weather_text output."""

REPLANNER_PROMPT = """You replan ONE rejected hiking day. You will receive: the day
number, start node, required end node (or "flexible"), the party constraints
(max_km, max_ascent_m, max_exposure), and the day's parsed weather.

Steps:
1. Call suggest_alternative_segments with the given constraints.
2. Re-score up to 3 returned candidates with assess_segment_risk using the
   provided weather.
3. If no candidate scores below no_go, relax ONCE and retry: first lower
   max_exposure one level (exposed_ridge -> mixed -> sheltered); if candidates
   are still empty, instead raise max_km by 20%. Do not relax further.
4. Reply with EXACTLY one JSON object:
   {"day": <n>, "status": "replanned"|"infeasible",
    "chosen": <best candidate object with its risk band, or null>,
    "relaxation_applied": "none"|"exposure"|"distance",
    "rationale": "<one sentence>"}
Prefix every message with [replan-day-<n>]."""

MAIN_SYSTEM_PROMPT = """You are TrailSmith, a weather-aware planner for multi-day
hikes in the Ukrainian Carpathians. You are given an itinerary (days with
segment IDs), a party profile, and dates. Follow this workflow strictly:

1. VALIDATE: call validate_itinerary. If any violation has severity "hard",
   stop and report the violations - do not continue. Soft violations: note
   them and continue with the normalized itinerary.
2. ASSESS: for EVERY day, spawn a day-assessor subagent. Give each one its
   day number, date, segments, and the day's end_settlement (from the
   normalized itinerary; for the forecast, day 1 uses its start; other days
   use their end_settlement). Spawn ALL day-assessors in ONE message so they
   run in parallel.
3. REPLAN: for each day whose band is "no_go", spawn a replanner subagent
   (all in one message, in parallel). Give it the day's start node, end node,
   the party's fitness caps as constraints (max_exposure starts at
   "exposed_ridge"), and the day's parsed weather.
4. MERGE: apply the chosen alternatives, then call validate_itinerary once on
   the full revised itinerary. If the merge introduced a hard violation,
   run at most one more replan round; after that, report the residual problem
   and recommend shifting dates.
5. LOGISTICS: call estimate_logistics with the final normalized itinerary.
6. REPORT: produce the final answer with: the final day-by-day plan; a per-day
   risk table (score, band, key factor); for every day that changed, WHY it
   changed, quoting the raw_forecast_excerpt verbatim so the value trace from
   the OpenWeather output to the decision is visible; logistics summary; and
   any days assessed with unknown weather (label them conservative estimates).

Never invent tool results. If a tool fails, say which one failed, show its
error, and degrade as described instead of stopping the whole run."""


def build_options(replay: bool) -> ClaudeAgentOptions:
    venv_python = sys.executable
    weather_bin = os.environ.get("OPENWEATHER_MCP_BIN") or str(
        REPO_ROOT / "bin" / "mcp-openweather.exe"
    )
    if replay:
        weather_server = {
            "type": "stdio",
            "command": venv_python,
            "args": [str(REPO_ROOT / "scripts" / "replay_weather_server.py")],
        }
    else:
        weather_server = {
            "type": "stdio",
            "command": weather_bin,
            "args": [],
            "env": {"OWM_API_KEY": os.environ.get("OWM_API_KEY", "")},
        }

    return ClaudeAgentOptions(
        cwd=str(REPO_ROOT),
        permission_mode="bypassPermissions",
        mcp_servers={
            "weather": weather_server,
            "trailsmith": {
                "type": "stdio",
                "command": venv_python,
                "args": ["-m", "trailsmith_mcp"],
                "cwd": str(REPO_ROOT),
            },
            "helpers": helpers_server,
        },
        allowed_tools=[
            "Task", VALIDATE_TOOL, LOGISTICS_TOOL,
            WEATHER_TOOL, PARSE_TOOL, RISK_TOOL, ALTERNATIVES_TOOL,
        ],
        system_prompt=MAIN_SYSTEM_PROMPT,
        agents={
            "day-assessor": AgentDefinition(
                description=(
                    "Assesses one hiking day: fetches the forecast for the "
                    "day's settlement, parses it, and scores the day's risk. "
                    "Spawn one per itinerary day, all in parallel."
                ),
                prompt=DAY_ASSESSOR_PROMPT,
                tools=[WEATHER_TOOL, PARSE_TOOL, RISK_TOOL],
            ),
            "replanner": AgentDefinition(
                description=(
                    "Replans one rejected hiking day: finds alternative "
                    "segment chains under constraints and re-scores them. "
                    "Spawn one per no_go day, all in parallel."
                ),
                prompt=REPLANNER_PROMPT,
                tools=[ALTERNATIVES_TOOL, RISK_TOOL],
            ),
        },
    )
