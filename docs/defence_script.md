# Defence script (10-15 minutes)

**Before you start**
- `.env` filled in (`OWM_API_KEY`; plus `ANTHROPIC_API_KEY` or a fresh `claude /login`).
- Bump every date in `demo/*.json` into the next 5 days, then re-run
  `.venv\Scripts\python scripts\fetch_fixtures.py`. Outside the forecast window
  every day degrades to "weather unknown", which is correct behaviour but not the
  clean run you want to show.
- All commands below use `.venv\Scripts\python` explicitly. With the venv
  unactivated, bare `python` is the system interpreter and every import fails.

## 0:00-2:00 — Independent startup and architecture

1. Terminal 1: `.venv\Scripts\python -m trailsmith_mcp` — the custom server
   starts alone. It prints nothing on success, so:
2. Terminal 2: `.venv\Scripts\python scripts\smoke_custom_server.py` — this is
   the visible proof: it connects from a *separate process* over stdio, lists all
   4 tools, makes one successful call and one structured-error call.
3. Architecture: agent process (Claude Agent SDK) ↔ two stdio MCP server
   processes (OpenWeather binary, trailsmith Python) ↔ data sources (OWM API,
   local GeoJSON dataset).
   **Say this before you are asked:** the run log will show a *third* connection,
   `agent_local`. It is an in-process agent-side helper holding the deterministic
   forecast parser; it is not part of Part B and does not count toward the tool
   requirement (`agent/helpers_server.py`, `docs/tool_contracts.md`).

## 2:00-5:00 — Existing MCP server in an agent flow

4. `.venv\Scripts\python -m agent.runner demo\itinerary_clean.json`
5. Point at the log: `TOOL CALL mcp__weather__weather {"city": "Vorokhta,UA"...}`
   inside a day-assessor, then `parse_weather_text`, then
   `assess_segment_risk` — the forecast values flow into the risk score.
6. Explain the `weather` contract from `docs/tool_contracts.md`: one tool, **text**
   output not JSON, and the notable finding that this server version *swallows*
   API errors — a bad key and an unknown city both return a successful all-zeros
   body, which we detect at the parsing boundary instead.

## 5:00-9:00 — Custom end-to-end workflow with subagents

7. The storm run, disclosed honestly:
   ```powershell
   $env:REPLAY=1; $env:FIXTURE_SET="scenario_storm"
   .venv\Scripts\python -m agent.runner demo\itinerary_storm.json
   ```
   **State plainly:** no thunderstorm was forecast when the fixtures were
   recorded, so `fixtures/scenario_storm/` contains one modified condition line
   in an otherwise genuine recording. It is an *input*, not a canned answer —
   the parser, the risk heuristics and the replanning search all run for real on
   it (`fixtures/scenario_storm/README.md`).
8. Narrate: validation passes → three `[day-N]` day-assessors spawn in one
   parallel batch → day 2 (Turkul ridge, `CH-008`+`CH-009`) scores **100 /
   no_go** on `thunderstorm_on_exposed_ridge` → `[replan-day-2]` proposes
   `CH-022`+`CH-014` (the 9.3 km valley bypass, **60 / caution**) → merged plan
   re-validates → logistics → final report.
9. Explain one contract in depth: `assess_segment_risk` — the constrained weather
   schema, the itemized `factors` list that makes the value trace possible, and
   the `weather_known` escape hatch. Good design decision to own: supplying a
   weather object *with* `weather_known=false` is **refused**
   (`CONTRADICTORY_WEATHER`) rather than silently ignored, because ignoring it
   would report a *lower* risk score for severe conditions.

## 9:00-11:00 — Failure scenario and replay mode

10. Break the key **in `.env`**, not in the shell — `agent/runner.py` calls
    `load_dotenv()`, so a cleared shell variable is overwritten from the file.
    Set `OWM_API_KEY=broken` in `.env`, then rerun the clean demo.
    Show: the degenerate all-zeros tool output → the parser's
    `NO_FORECAST_FOR_DATE` → `weather_known=false` → every day scored
    conservatively as `caution` with a `weather_unknown` factor → the final
    report labelling those days as conservative estimates. **The run completes**;
    the failure is surfaced, not hidden. Restore the key afterwards.
11. Offline replay of the genuine recordings:
    ```powershell
    $env:REPLAY=1; Remove-Item Env:\FIXTURE_SET -ErrorAction SilentlyContinue
    .venv\Scripts\python -m agent.runner demo\itinerary_clean.json
    ```
    Show that `fixtures/openweather/*.txt` are verbatim recorded tool text.

## 11:00-15:00 — Q&A and variations (prepared)

- **Changed valid input:** `demo\itinerary_lowfitness.json` — same route, party
  fitness `low` → one soft `DAILY_ASCENT_EXCEEDED` on day 1 ("climbs 790 m; cap
  for low fitness is 700 m") that the moderate party passed. Prefer this over
  the 5-day file: its violation has no date dependency.
- **Invalid input:** `demo\itinerary_invalid.json` → structured
  `{"error_code":"UNKNOWN_SEGMENT", ...}` at the first gate; the agent stops.
- **Value trace (the one that actually holds).** Do *not* use wind: real
  Carpathian wind is ~3 m/s against a 15 m/s threshold, so the wind rules never
  fire. Trace precipitation instead:
  `Conditions: Rain light rain` in the raw tool text →
  `precip_mm = 12.0` (keyword table in `agent/weather_parser.py`) →
  `swollen_river_crossings +10` on `CH-015` (1 crossing) → `risk_score 10`,
  band `ok`. Be upfront that on calm data the trace ends *without* changing the
  decision — that is the model working, not failing. The storm scenario is where
  it does change the decision.
- **Side effects:** all four custom tools are read-only over the local dataset.
  The only side effect in the system is the `weather` tool's outbound HTTPS call.
  Note the distinction precisely: the *agent process* is confined by
  `tools=["Task"]` plus `disallowed_tools` in `agent/orchestrator.py` — without
  that, the SDK would leave `Bash`/`Write` available, because `allowed_tools`
  only suppresses permission prompts and does not restrict availability.
- **Cost/loop bounds:** `max_budget_usd=1.50`, `max_turns=30`, per-subagent
  `maxTurns`. Real SDK limits, not prompt text; the runner prints turns and cost.
- **Known-limitation questions** are answered in `docs/design_rationale.md`:
  keyword-estimated precipitation, today's wind used for every day, the coarse
  `daylight_hours` month lookup, and the bounded alternative search.
