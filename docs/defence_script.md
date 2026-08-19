# Defence script (10-15 minutes)

Before the defence: `.env` filled in (ANTHROPIC_API_KEY, OWM_API_KEY), demo
dates adjusted to fall inside the next 5 days, fixtures re-recorded with
`python scripts/fetch_fixtures.py`.

## 0:00-2:00 — Independent startup and architecture

1. Terminal 1: `python -m trailsmith_mcp` — the custom server starts alone.
2. Terminal 2: `python scripts/smoke_custom_server.py` — shows discovery of
   all 4 tools over stdio from a separate process.
3. One-slide architecture: agent process (Claude Agent SDK) ↔ two stdio MCP
   servers (OpenWeather binary, trailsmith Python) ↔ data sources (OWM API,
   local GeoJSON dataset).

## 2:00-5:00 — Existing MCP server in an agent flow

4. `python -m agent.runner demo/itinerary_clean.json`
5. Point at the log lines: `TOOL CALL mcp__weather__weather {"city": "Vorokhta,UA"...}`
   inside a day-assessor, followed by `parse_weather_text` and
   `assess_segment_risk` — the forecast value flows into the risk score.
6. Explain the `weather` contract from docs/tool_contracts.md (single tool,
   text output, observed error behaviors).

## 5:00-9:00 — Custom end-to-end workflow with subagents

7. `python -m agent.runner demo/itinerary_storm.json` (ridge route on a bad
   day — if the real forecast is calm, use the recorded stormy fixture with
   `REPLAY=1` and say so).
8. Narrate: validation passes → three `[day-N]` day-assessors spawn in one
   parallel batch (timestamps in the log) → day 2 (Turkul ridge) scores
   `no_go` → `[replan-day-2]` replanner proposes CH-022+CH-014 bypass →
   merged plan re-validated → logistics → final report quotes the raw
   forecast excerpt next to the decisive risk factor.
9. Explain one contract in depth: `assess_segment_risk` (constrained weather
   schema, explicit heuristics, weather_known escape hatch).

## 9:00-11:00 — Failure scenario and replay mode

10. Stop; unset OWM_API_KEY (or set it to garbage); rerun the clean demo.
    Show the degenerate all-zeros tool output, the parser's
    `NO_FORECAST_FOR_DATE`, and the final report labeling every day
    "conservative estimate - weather unknown". Emphasize: the run completes
    and the failure is surfaced, not hidden.
11. `REPLAY=1 python -m agent.runner demo/itinerary_clean.json` — offline
    fixtures served through the identical parsing path; show
    `fixtures/openweather/*.txt` are verbatim recorded tool text.

## 11:00-15:00 — Q&A and variations (prepared)

- Changed valid input: `demo/itinerary_5day.json` (longer parallel batch,
  different forecasts) or `demo/itinerary_lowfitness.json` (same route, party
  fitness low → DAILY_ASCENT_EXCEEDED soft violations appear).
- Invalid input: `demo/itinerary_invalid.json` → UNKNOWN_SEGMENT structured
  error surfaces and the agent stops at validation.
- Value trace: wind speed in the raw `weather` text → parsed `wind_ms` →
  `high_wind_on_open_terrain` factor → day band → replan decision → final
  report line (the report embeds the raw excerpt verbatim).
- Side-effect question: all custom tools are read-only; the only side effect
  in the system is the outbound HTTPS call of the `weather` tool.
