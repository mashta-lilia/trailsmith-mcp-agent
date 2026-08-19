# Quickstart: from clone to a working workflow in ~10 minutes

**What you'll do**: install the project, start the custom MCP server on its own,
call its tools from a separate process, and run the full planning workflow —
including the storm case where the agent replans a dangerous day.

**What you'll learn**
- How the two MCP servers are started and discovered
- What the four custom tools return, and how errors look
- How a forecast value becomes a routing decision

**Prerequisites**
- [ ] Python 3.12+ (tested on 3.13)
- [ ] Go 1.21+ — only needed for step 4 (the live weather server)
- [ ] Windows PowerShell. On macOS/Linux, replace `.venv\Scripts\python` with
      `.venv/bin/python` and `$env:X="y"` with `X=y`.

Every command below was run against this repo; the output shown is real, trimmed
only where marked `...`.

---

## Step 1: Install

You only need Python for steps 1–3. Live weather comes later.

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

Verify:

```powershell
.venv\Scripts\python -m pytest tests -q
```

```
........................................                                 [100%]
40 passed in 1.46s
```

If those 40 pass, the domain logic and the forecast parser both work. Nothing
below can be broken by configuration you haven't done yet.

> **Tip**: use `.venv\Scripts\python` explicitly rather than activating the venv.
> With the venv unactivated, a bare `python` is the system interpreter and every
> `import mcp` fails.

## Step 2: Run the whole workflow with no credentials

Before touching API keys, confirm the domain workflow end to end. This script
calls the same rules in the same order as the agent, without an LLM:

```powershell
.venv\Scripts\python scripts\walkthrough.py demo\itinerary_storm.json --fixtures scenario_storm
```

```
[1] validate_itinerary -> ok
    caps applied: moderate (18.0 km, 1100 m per day)

[2] day 1 2026-08-20  ZAROSLYAK -> NESAMOVYTE  (6.6 km, 790 m)
    forecast: 2026-08-20 conditions='Clouds few clouds' low=12.85 high=12.85 wind=2.91
    risk: 0 ok

[2] day 2 2026-08-21  NESAMOVYTE -> BYSTRETS  (10.2 km, 500 m)
    forecast: 2026-08-21 conditions='Thunderstorm thunderstorm with heavy rain' ...
    risk: 100 no_go
      +70  thunderstorm_on_exposed_ridge: Thunderstorm forecast on an exposed ridge.
      +35  wet_exposed_ridge: 25.0 mm precipitation on an exposed ridge at 2036 m: ...
[3] day 2 is no_go - replanning
    chose ['CH-022', 'CH-014'] (9.3 km) -> 60 caution [relaxation: none]

[2] day 3 2026-08-22  BYSTRETS -> DZEMBRONIA  (4.6 km, 120 m)
    risk: 10 ok

[4] estimate_logistics  food_days=3 party_size=4
    day 1 2026-08-20: 3.0 h hiking, 13.0 h daylight margin, 0 water source(s)
    ...
```

Read what happened: the thunderstorm on the exposed Turkul ridge scored 100, the
planner found the 9.3 km valley bypass, re-scored it at 60 (`caution`), and
accepted it. That is the feedback loop the whole project is built around.

Try the calm case for contrast — same route, genuine recorded forecast:

```powershell
.venv\Scripts\python scripts\walkthrough.py demo\itinerary_storm.json
```

Every day comes back `ok`/`caution` and nothing is replanned. That is correct:
calm weather *should not* trigger a no-go. (See
[fixtures/scenario_storm/README.md](../fixtures/scenario_storm/README.md) for why
a synthetic input exists at all.)

## Step 3: Start the custom MCP server by itself

Terminal 1 — the server, alone, in its own process:

```powershell
.venv\Scripts\python -m trailsmith_mcp
```

It prints **nothing** on success and waits on stdin. That is normal for a stdio
MCP server; there is no banner to look for.

Terminal 2 — connect from a *separate* process and exercise it:

```powershell
.venv\Scripts\python scripts\smoke_custom_server.py
```

```
Discovered 4 tools:
  - validate_itinerary
  - assess_segment_risk
  - suggest_alternative_segments
  - estimate_logistics

validate_itinerary (valid input):
{
  "status": "ok",
  "normalized_itinerary": {
    "days": [
      {
        "date": "2026-09-12",
        "segments": ["CH-005", "CH-004"],
        "start_node": "ZAROSLYAK",
        "end_node": "NESAMOVYTE",
        "start_settlement": "Vorokhta,UA",
        "end_settlement": "Vorokhta,UA",
        "total_km": 6.6,
        "total_ascent_m": 790,
        "max_altitude_m": 1880,
        "ends_at_shelter": true
      }
    ]
  },
  "violations": [],
  "applied_caps": {"fitness": "moderate", "max_km": 18.0, "max_ascent_m": 1100}
}

assess_segment_risk (unknown segment) -> is_error = True
Error executing tool assess_segment_risk: {"error_code": "UNKNOWN_SEGMENT", "message": "Segment CH-999 not found in dataset", "detail": {"segment_ids": ["CH-999"]}}
```

Two things to notice. The tool **normalizes** — you passed two segment IDs and
got back the resolved start and end nodes, the costed distance and ascent, and
the caps that were applied. And an error is a *structured envelope*, not a
traceback: `error_code` is machine-readable, and it is clearly different from a
successful-but-empty result.

This script spawning the server itself is what demonstrates process separation:
the client and server never share a Python interpreter.

## Step 4: Add live weather (needs an API key)

Build the existing MCP server and configure keys:

```powershell
go install github.com/mschneider82/mcp-openweather@latest
New-Item -ItemType Directory -Force bin
Copy-Item "$env:USERPROFILE\go\bin\mcp-openweather.exe" bin\
Copy-Item .env.example .env
```

Put your free [OpenWeatherMap](https://home.openweathermap.org/api_keys) key into
`.env` as `OWM_API_KEY`. **A brand-new key takes up to ~2 hours to activate** —
until then the API returns 401.

```powershell
.venv\Scripts\python scripts\smoke_weather_server.py "Vorokhta,UA"
```

```
Tool: weather
Description: Get current and forecast weather information for a specific City
Input schema: {'properties': {'city': ..., 'lang': ..., 'units': ...}, 'required': ['city']}

weather('Vorokhta,UA') -> is_error = False
Current weather for Vorokhta:
    Conditions:  broken clouds
    Now:         19.74 metric
    ...
```

Note the output is **text, not JSON** — that is why the project has a dedicated
parser. If you instead see `Current weather for :` with all zeros, your key is
not active yet; see [troubleshooting](troubleshooting.md).

Record fixtures so you can work offline afterwards:

```powershell
.venv\Scripts\python scripts\fetch_fixtures.py
```

## Step 5: Run the agent

The agent needs Anthropic credentials — either `claude /login` in a terminal, or
`ANTHROPIC_API_KEY` in `.env`.

```powershell
.venv\Scripts\python -m agent.runner demo\itinerary_clean.json
```

You will see the workflow unfold in the log: the `validate_itinerary` call, then
`[day-N]`-tagged subagents each calling `mcp__weather__weather`,
`parse_weather_text` and `assess_segment_risk` in parallel, then the final report
with a per-day risk table and its turn count and dollar cost.

To run entirely offline against recorded fixtures:

```powershell
$env:REPLAY=1
.venv\Scripts\python -m agent.runner demo\itinerary_clean.json
```

## What you built

You now have a two-server MCP system where:

- **Weather affects routing.** A forecast value becomes a risk factor, the risk
  band gates replanning, and replanning changes the final plan.
- **Domain logic is deterministic.** Validation, scoring and route search are
  plain tested Python behind MCP contracts — the model sequences them, it does
  not perform them.
- **Failures degrade instead of crashing.** An unusable forecast becomes
  `weather_known=false` and a conservative `caution`, clearly labelled in the
  report.

## Next steps

- [Tool contracts](tool_contracts.md) — every schema, error code and example
- [Architecture](architecture.md) — process boundaries, workflow diagram, trust boundaries
- [Design rationale](design_rationale.md) — why each tool sits at the MCP boundary, and the honest limitations
- [Troubleshooting](troubleshooting.md) — when something above didn't behave as shown
