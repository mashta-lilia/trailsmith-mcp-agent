# TrailSmith — weather-aware Carpathian itinerary agent (MCP assignment)

A domain-specific data agent that validates, risk-assesses, and replans
multi-day hiking itineraries in the Chornohora range. It uses two MCP
connections:

- **Existing server (Part A):** [OpenWeather MCP](https://github.com/mschneider82/mcp-openweather) — live 5-day forecasts per settlement.
- **Custom server (Part B):** `trailsmith` (this repo, `trailsmith_mcp/`) — four domain tools over a local curated trail dataset.

The agent (Claude Agent SDK) validates an itinerary, spawns one **day-assessor
subagent per day in parallel** (forecast → deterministic parse → risk score),
spawns **replanner subagents** for `no_go` days, merges and re-validates, and
produces a final plan with a visible value trace from raw forecast text to the
decision.

## Prerequisites

- Python 3.12+ (tested on 3.13)
- Go 1.21+ (only to build the OpenWeather MCP binary)
- An OpenWeatherMap API key (free tier) and an Anthropic API key

## Installation

PowerShell, from the repo root:

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
go install github.com/mschneider82/mcp-openweather@latest
New-Item -ItemType Directory -Force bin
Copy-Item "$env:USERPROFILE\go\bin\mcp-openweather.exe" bin\
Copy-Item .env.example .env
```

Then edit `.env`:

| Variable | Needed for |
|---|---|
| `ANTHROPIC_API_KEY` | the agent (Claude Agent SDK). Optional if you have run `claude /login`. |
| `OWM_API_KEY` | live OpenWeather calls. A new key takes up to ~2 h to activate. |
| `OPENWEATHER_MCP_BIN` | optional override; defaults to `bin\mcp-openweather.exe`. |
| `REPLAY` | `1` to serve recorded fixtures instead of the live API. |
| `FIXTURE_SET` | optional replay directory name; defaults to `openweather`. |

Secrets live only in `.env` (git-ignored, along with any `.env.*` variant).
Nothing sensitive is committed.

## Independent start commands

- **Custom MCP server (separate process):** `.venv\Scripts\python -m trailsmith_mcp`
- **Agent:** `.venv\Scripts\python -m agent.runner demo\itinerary_clean.json`
- Smoke tests: `.venv\Scripts\python scripts\smoke_custom_server.py` (discovers and
  calls the custom server over stdio), `.venv\Scripts\python scripts\smoke_weather_server.py [city]`
  (existing server).

The agent starts its own MCP connections; starting `trailsmith_mcp` manually
demonstrates process separation and independent startability.

## Demo inputs

| File | Purpose |
|---|---|
| `demo/itinerary_clean.json` | 3-day valley/mixed traverse — clean pass |
| `demo/itinerary_storm.json` | Day 2 crosses the exposed Turkul ridge. Replanning only fires under stormy weather — run it with `FIXTURE_SET=scenario_storm` (see below) |
| `demo/itinerary_invalid.json` | Unknown segment ID — structured error demo |
| `demo/itinerary_5day.json` | Changed valid input: 5 days, high fitness |
| `demo/itinerary_lowfitness.json` | Changed valid input: low fitness → one soft `DAILY_ASCENT_EXCEEDED` violation on day 1 |

**The demo dates must fall inside the live 5-day forecast window.** They are set
for 2026-08-20 onward; before a live demo, bump every `date` and re-run
`scripts/fetch_fixtures.py`. Outside the window the parser correctly raises
`NO_FORECAST_FOR_DATE` and the day degrades to `weather_known=false` /
`caution` — a legitimate path, but not the one you want to present as the clean
run.

## Fixtures and offline replay

- Record genuine responses: `.venv\Scripts\python scripts\fetch_fixtures.py`
  saves verbatim tool text to `fixtures/openweather/`. It also records the
  invalid-city response — which this server returns as a *successful* all-zeros
  body rather than an error, so it is saved as a normal `.txt`. There is no
  `.error.txt` fixture, because the upstream server never produced one.
- Replay offline: set `REPLAY=1` and run the agent normally. The replay server
  (`scripts/replay_weather_server.py`) exposes the same `weather` contract and
  serves the recorded text verbatim — the agent's parsing and error handling run
  unchanged; nothing is pre-parsed.

  ```powershell
  $env:REPLAY=1
  .venv\Scripts\python -m agent.runner demo\itinerary_clean.json
  ```
- **Storm scenario.** `no_go` risk requires a thunderstorm or severe conditions,
  which Carpathian forecasts frequently lack — on a calm day the agent correctly
  reports `ok`/`caution` and never replans, so the replanning branch cannot be
  demonstrated on demand. `fixtures/scenario_storm/` holds a **clearly labelled
  synthetic input** (one condition line changed in an otherwise genuine
  recording) for exercising that branch; see its README, and disclose it when
  demonstrating. Select it with:

  ```powershell
  $env:REPLAY=1; $env:FIXTURE_SET="scenario_storm"
  .venv\Scripts\python -m agent.runner demo\itinerary_storm.json
  ```

## Rate limits

OpenWeatherMap free tier allows 60 calls/min. What actually bounds our call
volume:

- `validate_itinerary` rejects itineraries longer than **7 days**
  (`Itinerary.days` has `max_length=7`), so at most 7 day-assessors are spawned.
- Each day-assessor is capped at `maxTurns=6`, so it cannot loop on the weather
  API after a failure.
- Worst case is therefore well under the per-minute limit. The *width* of the
  parallel batch is decided by the Claude Code CLI's Task scheduler, not by this
  code — we bound the total number of calls, not their concurrency.

Cost guardrails (`agent/orchestrator.py`): `max_budget_usd=1.50` and
`max_turns=30` on the main loop, plus per-subagent `maxTurns`. These are hard
SDK limits, not prompt instructions; `agent/runner.py` prints the run's turn
count and dollar cost, and flags a run that stopped on a cap.

## Tests

```
.venv\Scripts\python -m pytest tests -q
```

40 unit tests cover validation rules, risk heuristics, graph search, logistics,
the forecast-text parser (including malformed input), and regression
tests for every contract and correctness bug found during review.

## Documentation

- [docs/tool_contracts.md](docs/tool_contracts.md) — full Part C contracts for
  all four custom tools and the observed `weather` contract.
- [docs/design_rationale.md](docs/design_rationale.md) — boundary decisions,
  trade-offs, limitations.
- [docs/defence_script.md](docs/defence_script.md) — timed demo script.
- [data/PROVENANCE.md](data/PROVENANCE.md) — dataset origin and reproduction
  (`scripts/build_dataset.py` regenerates it).
