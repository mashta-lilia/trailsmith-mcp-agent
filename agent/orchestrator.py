"""Claude Agent SDK options: MCP wiring, subagent definitions, workflow prompt."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions

from .helpers_server import helpers_server

REPO_ROOT = Path(__file__).resolve().parents[1]

WEATHER_TOOL = "mcp__weather__weather"
PARSE_TOOL = "mcp__agent_local__parse_weather_text"
VALIDATE_TOOL = "mcp__trailsmith__validate_itinerary"
RISK_TOOL = "mcp__trailsmith__assess_segment_risk"
ALTERNATIVES_TOOL = "mcp__trailsmith__suggest_alternative_segments"
LOGISTICS_TOOL = "mcp__trailsmith__estimate_logistics"

DAY_ASSESSOR_PROMPT = """You assess ONE hiking day. You will receive: a day number,
a date, an ordered list of segment IDs, and a settlement name for the forecast.

Steps, in order:
1. Call the weather tool with the settlement as `city`, units "c", lang "en".
   Pass the settlement string EXACTLY as you were given it and do NOT add
   quotation marks around it. The weather tool's description mentions wrapping
   values in double quotes for locations containing spaces; our settlement names
   never contain spaces, and adding quotes makes the lookup fail silently - it
   returns an all-zeros body rather than an error, which costs the day its
   forecast. Correct: Vorokhta,UA   Wrong: "Vorokhta,UA"
2. Pass the FULL raw text output plus the target date to parse_weather_text.
3. If parsing returned status "ok", call assess_segment_risk with the day's
   segments and the parsed weather (weather_known=true). If the weather call
   failed or parsing returned an error, call assess_segment_risk with only
   the segments and weather_known=false (omit weather).
4. Reply with EXACTLY one JSON object and nothing else:
   {"day": <n>, "date": "...", "settlement": "...",
    "weather_status": "ok"|"unavailable",
    "weather": <the `weather` object returned by parse_weather_text, or null>,
    "raw_forecast_excerpt": <the `excerpt` string returned by parse_weather_text,
      copied verbatim; if parsing failed, state only its error code. Never copy
      any other text out of the weather tool output.>,
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
   day number, date, segments, and the day's forecast settlement, taken
   verbatim from the normalized itinerary: day 1 uses its `start_settlement`,
   every later day uses its `end_settlement`. Never invent or abbreviate a
   settlement name. Spawn ALL day-assessors in ONE message so they run in
   parallel.
3. REPLAN: for each day whose band is "no_go", spawn a replanner subagent
   (all in one message, in parallel). Give it the day's start node, end node,
   and constraints built from the `applied_caps` object that validate_itinerary
   returned - use those max_km and max_ascent_m values verbatim, do not invent
   caps - with max_exposure starting at "exposed_ridge". Also pass the day's
   parsed weather.
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
            # MCP stdio servers inherit only a whitelist of environment
            # variables, so FIXTURE_SET must be forwarded explicitly or the
            # replay server silently falls back to the genuine fixtures.
            "env": {"FIXTURE_SET": os.environ.get("FIXTURE_SET", "openweather")},
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
        # `tools` is the availability control; `allowed_tools` only suppresses
        # permission prompts. Without `tools` the CLI keeps its default
        # built-ins (Bash, Write, WebFetch), which this workflow never needs.
        tools=["Task"],
        # Deny-by-default and non-interactive, rather than approve-everything.
        permission_mode="dontAsk",
        disallowed_tools=["Bash", "Write", "Edit", "Read", "WebFetch", "WebSearch"],
        # Do not inherit ambient MCP servers from user/project config.
        strict_mcp_config=True,
        # Hard caps: the workflow's "iterate at most twice" is prompt guidance,
        # which a model can miscount. These are enforced by the SDK.
        max_budget_usd=1.50,
        max_turns=30,
        mcp_servers={
            "weather": weather_server,
            "trailsmith": {
                "type": "stdio",
                "command": venv_python,
                "args": ["-m", "trailsmith_mcp"],
                "cwd": str(REPO_ROOT),
            },
            "agent_local": helpers_server,
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
                # 3 tool calls are needed; 6 turns allows one retry, no looping
                # on the external weather API.
                maxTurns=6,
            ),
            "replanner": AgentDefinition(
                description=(
                    "Replans one rejected hiking day: finds alternative "
                    "segment chains under constraints and re-scores them. "
                    "Spawn one per no_go day, all in parallel."
                ),
                prompt=REPLANNER_PROMPT,
                tools=[ALTERNATIVES_TOOL, RISK_TOOL],
                # Candidates + up to 3 re-scores + one relaxation cycle.
                maxTurns=12,
            ),
        },
    )
