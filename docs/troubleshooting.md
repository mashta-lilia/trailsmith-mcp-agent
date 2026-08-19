# Troubleshooting

Every symptom below was observed while building this project. Each entry gives
the exact output you'll see, the cause, and the fix.

## The agent won't start

### `Failed to authenticate: OAuth session expired and could not be refreshed`

```
[    3.4s] AGENT FAILED: Claude Code returned an error result: Failed to
           authenticate: OAuth session expired and could not be refreshed
[    4.3s] Hint: run `claude /login`, or set ANTHROPIC_API_KEY in .env
```

The Claude Agent SDK falls back to your Claude Code CLI login when
`ANTHROPIC_API_KEY` is unset, and that session has expired. Either run
`claude /login` in a normal terminal and complete the browser sign-in, or put an
`ANTHROPIC_API_KEY` in `.env`.

Everything except the agent works without this — use
`scripts/walkthrough.py` (see [quickstart step 2](quickstart.md)) to exercise the
full domain workflow meanwhile.

### `ModuleNotFoundError: No module named 'mcp'`

You are running the system Python instead of the venv. Use the interpreter
explicitly:

```powershell
.venv\Scripts\python -m agent.runner demo\itinerary_clean.json
```

This also affects the *server*: `scripts/smoke_custom_server.py` passes
`sys.executable` to the child process, so launching the script with the wrong
interpreter makes the spawned server fail too.

### `RUN STOPPED EARLY: error_max_budget_usd`

The run hit the `max_budget_usd=1.50` ceiling in `agent/orchestrator.py`. This is
a deliberate guardrail, not a bug — the output you got is a truncated run, not a
finished answer. Either raise the cap or shorten the itinerary. A clean 3-day run
normally costs well under the cap.

## Weather problems

### All zeros: `Current weather for :` with every value `0`

```
Current weather for :
    Conditions:
    Now:         0 metric
    ...
Weather Forecast for :
```

**This is what an API failure looks like** — the upstream server does not return
an error. Both an invalid/missing `OWM_API_KEY` and an unknown city produce this
identical successful-looking response with `is_error = false`.

Causes, in order of likelihood:

1. **A brand-new API key that hasn't activated yet.** OpenWeatherMap keys take up
   to ~2 hours. Verify directly:

   ```powershell
   curl "https://api.openweathermap.org/data/2.5/weather?q=Vorokhta,UA&appid=YOUR_KEY&units=metric"
   ```

   `{"cod":401, "message": "Invalid API key..."}` means not yet active — wait.
2. **`OWM_API_KEY` missing from `.env`.**
3. **A city name OpenWeatherMap can't resolve.** The dataset uses only four
   verified names (`Vorokhta,UA`, `Yasinia,UA`, `Rakhiv,UA`, `Verkhovyna,UA`).

The agent handles this correctly rather than crashing: the parser rejects the
text with `NO_FORECAST_FOR_DATE`, the day is scored with `weather_known=false`,
and the report labels it a conservative estimate.

### Every day reports "weather unknown" even though the API works

Your itinerary dates are outside the 5-day forecast window. The window is only
today + 5 days; `demo/*.json` ships with fixed dates that expire.

Check what the fixtures actually cover:

```powershell
Select-String -Path fixtures\openweather\vorokhta_ua.txt -Pattern "Date & Time" |
  ForEach-Object { ($_ -split ' ')[3] } | Sort-Object -Unique
```

Fix: bump every `date` in the demo files into the next five days, then re-record:

```powershell
.venv\Scripts\python scripts\fetch_fixtures.py
```

If you also want the storm scenario, regenerate it too — its modified condition
line is pinned to a specific date (see
[fixtures/scenario_storm/README.md](../fixtures/scenario_storm/README.md)).

### `replay mode: no fixture recorded for city 'X' in fixtures/openweather/`

The requested city has no `.txt` fixture. Either run `scripts/fetch_fixtures.py`
with a live key, or check that `FIXTURE_SET` points at the directory you meant.

Fixture filenames are slugs of the city string: `Vorokhta,UA` → `vorokhta_ua.txt`.
The dataset stores `Vorokhta,UA` with the `,UA` suffix precisely so the dataset,
the recorder and the replay server all derive the same key.

### `REPLAY=1` had no effect

In PowerShell, `REPLAY=1 python ...` is not valid syntax — it is a POSIX
env-prefix. Set the variable first:

```powershell
$env:REPLAY=1
.venv\Scripts\python -m agent.runner demo\itinerary_clean.json
```

Also note `agent/runner.py` calls `load_dotenv()`, so a value in `.env` is loaded
into the process. To force live mode, set `REPLAY=0` in `.env` *and* clear the
shell variable: `Remove-Item Env:\REPLAY -ErrorAction SilentlyContinue`.

## Tool errors

### `{"error_code": "UNKNOWN_SEGMENT", ...}`

A segment ID isn't in the dataset. Valid IDs are `CH-001` … `CH-024`; see
`data/trails.geojson`. `demo/itinerary_invalid.json` triggers this deliberately.

### `{"error_code": "MISSING_WEATHER", ...}` / `CONTRADICTORY_WEATHER`

`assess_segment_risk` requires exactly one of two shapes:

- a `weather` object with `weather_known` true (the default), or
- **no** `weather` object with `weather_known: false`.

Supplying both is refused on purpose — silently ignoring the weather would return
a *lower* risk score for genuinely severe conditions.

### `{"error_code": "DISCONNECTED_DAY", ...}` from `estimate_logistics`

A day's segments don't form a connected chain. Run `validate_itinerary` first and
fix the violations; `estimate_logistics` recomputes distances from `segments`, so
it cannot cost a broken chain.

### A pydantic error with no `error_code`

```
Error executing tool validate_itinerary: 1 validation error for
validate_itineraryArguments ... String should match pattern '^\d{4}-...'
```

This is a *schema rejection*, raised before the tool body runs — a bad type,
out-of-range value, malformed date, repeated segment, or unknown field. It is
still `is_error = true`, but it carries no `error_code`. Both shapes are documented
in [tool_contracts.md](tool_contracts.md#how-errors-are-represented-custom-server).

### An empty `candidates` list — is that an error?

No. `{"status": "ok", "candidates": []}` means the constraints are genuinely
infeasible, and it is deliberately distinguishable from a failure. Loosen
`max_exposure`, `max_km` or `max_ascent_m`.

## Dataset and setup

### `FileNotFoundError: data/trails.geojson`

Regenerate it — the file is committed, but this rebuilds it deterministically:

```powershell
.venv\Scripts\python scripts\build_dataset.py
```

```
Wrote 18 nodes, 24 segments, 9 shelters to ...\data
```

### `Copy-Item ... bin\` fails

`bin/` is git-ignored, so it doesn't exist in a fresh clone. Create it first:

```powershell
New-Item -ItemType Directory -Force bin
```

### `python -m trailsmith_mcp` prints nothing

That is success. A stdio MCP server has no banner; it waits on stdin. Prove it is
alive from another terminal with `scripts\smoke_custom_server.py`.

### Non-ASCII characters render as `?` or crash the console

`agent/runner.py` forces UTF-8 on stdout for this reason. If you see it elsewhere,
set `$env:PYTHONIOENCODING="utf-8"`.

## Still stuck?

Run the two smoke tests and the walkthrough — between them they isolate almost
every layer:

```powershell
.venv\Scripts\python -m pytest tests -q                          # domain logic + parser
.venv\Scripts\python scripts\walkthrough.py demo\itinerary_clean.json   # full workflow, no auth
.venv\Scripts\python scripts\smoke_custom_server.py              # custom MCP server over stdio
.venv\Scripts\python scripts\smoke_weather_server.py "Vorokhta,UA"  # existing MCP server + API key
```

The first failure in that list tells you which layer to look at.
