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

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
go install github.com/mschneider82/mcp-openweather@latest
copy "%USERPROFILE%\go\bin\mcp-openweather.exe" bin\
copy .env.example .env   # then fill in ANTHROPIC_API_KEY and OWM_API_KEY
```

Secrets live only in `.env` (git-ignored). Nothing sensitive is committed.

## Independent start commands

- **Custom MCP server (separate process):** `.venv\Scripts\python -m trailsmith_mcp`
- **Agent:** `.venv\Scripts\python -m agent.runner demo\itinerary_clean.json`
- Smoke tests: `scripts\smoke_custom_server.py` (discovers/calls the custom
  server over stdio), `scripts\smoke_weather_server.py [city]` (existing server).

The agent starts its own MCP connections; starting `trailsmith_mcp` manually
demonstrates process separation and independent startability.

## Demo inputs

| File | Purpose |
|---|---|
| `demo/itinerary_clean.json` | 3-day valley/mixed traverse — clean pass |
| `demo/itinerary_storm.json` | Same, but day 2 crosses the Turkul ridge — replanning demo |
| `demo/itinerary_invalid.json` | Unknown segment ID — structured error demo |
| `demo/itinerary_5day.json` | Changed valid input: 5 days, high fitness |
| `demo/itinerary_lowfitness.json` | Changed valid input: low fitness → soft violations |

**Adjust the `date` fields to fall within the next 5 days before a live demo**
(the forecast window), then re-record fixtures.

## Fixtures and offline replay

- Record genuine responses: `.venv\Scripts\python scripts\fetch_fixtures.py`
  (saves verbatim tool text to `fixtures/openweather/`, plus one genuine
  error response for an invalid city).
- Replay offline: set `REPLAY=1` in `.env` (or the environment) and run the
  agent normally. The replay server (`scripts/replay_weather_server.py`)
  exposes the same `weather` contract and serves the recorded text verbatim —
  the agent's parsing and error handling run unchanged; nothing is pre-parsed.

## Rate limits

OpenWeatherMap free tier allows 60 calls/min. Day-assessor concurrency is
`min(5, len(days))` and each run makes at most 7 weather calls.

## Tests

```
.venv\Scripts\python -m pytest tests -q
```

20 unit tests cover validation rules, risk heuristics, graph search, logistics,
and the forecast-text parser (including malformed input).

## Documentation

- [docs/tool_contracts.md](docs/tool_contracts.md) — full Part C contracts for
  all four custom tools and the observed `weather` contract.
- [docs/design_rationale.md](docs/design_rationale.md) — boundary decisions,
  trade-offs, limitations.
- [docs/defence_script.md](docs/defence_script.md) — timed demo script.
- [data/PROVENANCE.md](data/PROVENANCE.md) — dataset origin and reproduction
  (`scripts/build_dataset.py` regenerates it).
