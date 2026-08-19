# Architecture

## The problem this shape solves

A hiker asks: *"is this 3-day Chornohora route safe on these dates?"* Answering
it needs two things that live in different places — **domain rules over a trail
dataset** (does the route connect? is the ascent within the party's limits? how
exposed is it?) and **live external weather**. Neither alone is enough, and the
answer is not a lookup: the forecast changes the risk, the risk decides whether
the route must change, and a changed route has to be re-validated.

So the system is split along that seam. Two MCP servers own the two data
domains; an agent owns the sequencing between them.

## Process boundaries

```
┌─────────────────────────────────────────────────────────────────────┐
│ AGENT PROCESS            python -m agent.runner demo/*.json         │
│                                                                     │
│   agent/runner.py        reads the request JSON, logs, cost caps     │
│   agent/orchestrator.py  MCP wiring, subagent defs, workflow prompt  │
│   agent/weather_parser.py  deterministic forecast-text parser        │
│                                                                     │
│   ┌───────────────────┐   ┌───────────────────┐                     │
│   │ day-assessor ×N   │   │ replanner ×M      │   subagents,         │
│   │ (one per day)     │   │ (one per no_go)   │   spawned in         │
│   └───────────────────┘   └───────────────────┘   parallel batches   │
│                                                                     │
│   ┌─────────────────────────────────────────┐                       │
│   │ agent_local (IN-PROCESS SDK server)     │  not part of Part B    │
│   │   parse_weather_text                    │  — agent-side helper   │
│   └─────────────────────────────────────────┘                       │
└──────────┬──────────────────────────────┬───────────────────────────┘
           │ stdio / MCP                  │ stdio / MCP
           ▼                              ▼
┌──────────────────────────┐   ┌────────────────────────────────────────┐
│ EXISTING SERVER (Part A) │   │ CUSTOM SERVER (Part B)                 │
│ bin/mcp-openweather.exe  │   │ python -m trailsmith_mcp               │
│ (Go binary, separate     │   │ (separate process, independently        │
│  process)                │   │  startable)                             │
│                          │   │                                        │
│ tool: weather            │   │ validate_itinerary                     │
│                          │   │ assess_segment_risk                    │
│                          │   │ suggest_alternative_segments           │
│                          │   │ estimate_logistics                     │
└──────────┬───────────────┘   └──────────────┬─────────────────────────┘
           │ HTTPS                            │ read-only file I/O
           ▼                                  ▼
   api.openweathermap.org          data/trails.geojson  (18 nodes,
   (5-day forecast, text out)      data/shelters.csv     24 segments,
                                                         9 shelters)
                                   built once at startup into a
                                   NetworkX graph (~0.6 ms)
```

**Why three connections when the assignment asks for two.** `agent_local` is an
in-process SDK MCP server holding one thing: the forecast-text parser. It has to
be a *tool* because an LLM subagent must be able to invoke it, but it belongs to
the agent, not to the hiking domain. Putting it in `trailsmith_mcp` would couple
the domain server to one upstream server's prose format. It does not count toward
Part B's three-tool requirement, and it is named `agent_local` precisely so that
a reader of the run log cannot mistake it for the custom server.

## The workflow, and where each result is consumed

```
  request JSON (days + party + dates)
        │
        ▼
  ┌───────────────────────┐
  │ validate_itinerary    │  custom server
  └───────────┬───────────┘
              │ status, normalized_itinerary, violations, applied_caps
              ▼
        hard violation? ──yes──► STOP, report violations, ask for a fix
              │ no
              ▼
  ┌─────────────────────────────────────────────────┐
  │ per day, IN PARALLEL: day-assessor subagent      │
  │   weather(city)              ← existing server   │
  │   parse_weather_text(text)   ← agent_local       │
  │   assess_segment_risk(...)   ← custom server     │
  └───────────┬─────────────────────────────────────┘
              │ risk band per day + bounded forecast excerpt
              ▼
        any band == no_go? ──no──────────────────────┐
              │ yes                                  │
              ▼                                      │
  ┌─────────────────────────────────────────────────┐│
  │ per failing day, IN PARALLEL: replanner subagent ││
  │   suggest_alternative_segments  ← custom server  ││
  │   assess_segment_risk (re-score) ← custom server ││
  │   relax once if still no_go:                     ││
  │     exposure down one level, then max_km +20%     ││
  └───────────┬─────────────────────────────────────┘│
              │ chosen alternatives                   │
              ▼                                       │
  ┌───────────────────────┐                           │
  │ validate_itinerary    │  re-validate the merge    │
  │ (again)               │  — the one cross-day      │
  └───────────┬───────────┘    dependency             │
              │◄──────────────────────────────────────┘
              ▼
  ┌───────────────────────┐
  │ estimate_logistics    │  custom server
  └───────────┬───────────┘
              ▼
     final report: plan, per-day risk table, what changed and why
     (quoting the forecast excerpt), logistics, weather-unknown days
```

Nothing in that chain is decorative. `normalized_itinerary` feeds both the
day-assessors and `estimate_logistics`; `applied_caps` becomes the replanner's
constraints, so the model never invents limits; risk bands gate replanning;
candidate routes are re-scored before being accepted; the second validation
catches a merge that broke day-to-day connectivity.

## The value trace

The requirement that a tool result must affect later steps is satisfied by one
concrete path, and it is worth being able to point at each hop:

| Hop | Where | Value |
|---|---|---|
| Raw external text | `weather` tool output | `Conditions: Thunderstorm thunderstorm with heavy rain` |
| Deterministic parse | `agent/weather_parser.py` | `thunderstorm=True, precip_mm=25.0` |
| Domain scoring | `assess_segment_risk` | `+70 thunderstorm_on_exposed_ridge`, `+35 wet_exposed_ridge` → **100 / no_go** |
| Decision | main agent | day 2 must change |
| Search | `suggest_alternative_segments` | `["CH-022","CH-014"]`, 9.3 km valley bypass |
| Re-score | `assess_segment_risk` | **60 / caution** — accepted |
| Report | final answer | the change, quoting the excerpt above |

Run it yourself with no credentials:

```powershell
.venv\Scripts\python scripts\walkthrough.py demo\itinerary_storm.json --fixtures scenario_storm
```

## Why the parser is deterministic, not model-driven

The `weather` tool returns **prose, not JSON** — and its forecast entries carry
no wind and no precipitation figure. Extracting numbers from that is exactly the
kind of task an LLM does plausibly but not reliably, and a wrong number here
silently changes a safety verdict. So extraction is a tested regex parser with
range validation that **fails closed**: on a parse failure the day is scored with
`weather_known=false`, which forces a conservative `caution` band rather than
inventing a forecast.

This also matters for the upstream server's most surprising behaviour: a missing
API key and an unknown city both return a *successful* all-zeros response. There
is no error to catch, so the failure is detected at the parsing boundary instead.

## Trust boundaries

| Boundary | What crosses it | Control |
|---|---|---|
| OpenWeather API → weather server | forecast prose | none available (upstream Go binary) |
| weather server → agent | untrusted external text | parsed to five numeric fields; the report quotes only a bounded, parser-generated excerpt, never free text |
| agent → subagents | task instructions | each subagent gets a minimal `tools` allowlist (3 and 2 tools) |
| agent → host machine | tool availability | `tools=["Task"]` plus `disallowed_tools`; without `tools` set, the SDK would leave `Bash`/`Write` available, because `allowed_tools` only suppresses prompts |
| agent → cost | turns and spend | `max_budget_usd`, `max_turns`, per-subagent `maxTurns` — SDK-enforced, not prompt text |
| tools → dataset | file reads | opened read-only; all four tools are side-effect-free |

## Where to read the code

| Concern | File |
|---|---|
| Dataset loading, graph build, segment geometry | `trailsmith_mcp/dataset.py` |
| All domain rules — caps, validation, risk, search, logistics | `trailsmith_mcp/rules.py` |
| MCP tool definitions, schemas, error envelopes | `trailsmith_mcp/server.py` |
| Standalone server entry point | `trailsmith_mcp/__main__.py` |
| MCP wiring, subagent definitions, workflow prompt, guardrails | `agent/orchestrator.py` |
| Run loop, logging, cost reporting | `agent/runner.py` |
| Forecast-text parsing and validation | `agent/weather_parser.py` |
| Offline replay server | `scripts/replay_weather_server.py` |

`rules.py` is deliberately pure and dataset-injected: it imports no MCP code, so
every heuristic is unit-testable without starting a server. That is why the test
suite can cover the domain logic directly.
