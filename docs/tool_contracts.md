# Tool-contract documentation

## How errors are represented (custom server)

There are **two** error shapes, and a caller must handle both:

1. **Domain errors** — raised deliberately by a tool. The MCP result has
   `is_error = true` and its text is the framework prefix followed by our JSON
   envelope:

   ```
   Error executing tool validate_itinerary: {"error_code": "UNKNOWN_SEGMENT", "message": "Segment CH-999 not found in dataset", "detail": {"segment_ids": ["CH-999"]}}
   ```

   Note the `Error executing tool <name>: ` prefix added by the `mcp` framework —
   `json.loads(text)` on the whole string fails; strip the prefix first.

2. **Schema rejections** — raised by pydantic before the tool body runs (wrong
   type, out-of-range value, bad date, unknown field). These carry a pydantic
   validation message and **no `error_code`**. They are still `is_error = true`.

An error is always distinguishable from a successful empty result:
`suggest_alternative_segments` with impossible constraints returns a normal
result `{"status": "ok", "candidates": []}`.

---

## Custom server: `trailsmith` (Part B / Part C)

### 1. `validate_itinerary`

| Element | Content |
|---|---|
| Name | `validate_itinerary` |
| Purpose | Validate a proposed multi-day itinerary against the trail dataset and party constraints. Called first, before any risk assessment or replanning, and again after merging replanned days. |
| Model-facing description | "Validate a multi-day Carpathian hiking itinerary. Checks that trail segments exist and connect, daily distance and ascent respect the party's fitness caps, and each night ends at a shelter or legal camp (or the party carries a tent). Returns a normalized itinerary and a list of violations. Call this before assessing risk or suggesting alternatives." |
| Input schema | `itinerary: {days: [{date: string, segments: [string]}]}` — 1–7 days; `date` must match `^\d{4}-(0[1-9]\|1[0-2])-(0[1-9]\|[12]\d\|3[01])$`; `segments` 1–20 items matching `^CH-\d{3}$`, **must be distinct within a day**. `party: {fitness: "low"\|"moderate"\|"high", size: int 1..12, has_tent: bool}`. Both required. |
| Output schema | `{status: "ok"\|"invalid", normalized_itinerary: {days: [...]}, violations: [{code, severity, day, message}], applied_caps: {fitness, max_km, max_ascent_m}}` |
| | Each normalized day: `date, segments, start_node, end_node, start_settlement, end_settlement, total_km, total_ascent_m, max_altitude_m, ends_at_shelter`. `segments` reflects the **orientation actually costed**, which may be reversed relative to the input when the day chains onto the previous one. For a `DISCONNECTED_DAY` the node/settlement fields are `null` and the numeric fields `0` — the day keeps the same key set so downstream tools fail on the domain violation, not on a schema error. |
| | `applied_caps` publishes the caps the server actually applied, so the caller (and the replanner subagent) never has to invent constraint values. |
| Violation codes | `DISCONNECTED_DAY` (hard), `DAY_BOUNDARY_GAP` (hard), `DATE_SEQUENCE_INVALID` (hard — dates must strictly increase), `NO_SHELTER_AT_NIGHT` (hard), `DAILY_DISTANCE_EXCEEDED` (soft), `DAILY_ASCENT_EXCEEDED` (soft). `status:"invalid"` is a *successful* validation that found problems — not an error. |
| Error conditions | `UNKNOWN_SEGMENT` (segment not in dataset); schema rejection for a malformed date, a repeated segment within a day, >7 days, or an out-of-range party size. |
| Side effects | None (read-only over the local dataset). |
| Example | Input: 1 day `["CH-005","CH-004"]`, party moderate/4/tent → `status:"ok"`, day normalized to start `ZAROSLYAK`, end `NESAMOVYTE`, 6.6 km, 790 m ascent, `ends_at_shelter: true`, `applied_caps: {fitness:"moderate", max_km:18.0, max_ascent_m:1100}`, `violations: []`. |

### 2. `assess_segment_risk`

| Element | Content |
|---|---|
| Name | `assess_segment_risk` |
| Purpose | Compute a weather-conditioned risk score for one day's segments. Called by day-assessor subagents after parsing a forecast, and by replanner subagents to re-score candidates. |
| Model-facing description | "Assess the risk of hiking a given day's trail segments under a provided weather summary. Combines segment exposure, altitude, and river crossings with wind, precipitation, temperature, and thunderstorm risk using explicit mountain-safety heuristics. Returns a 0-100 risk score, a band (ok/caution/no_go), and itemized factors. When no reliable forecast is available, set weather_known=false and omit weather." |
| Input schema | `segments: [string]` (required, ≥1). `weather: {temp_min_c: -40..45, temp_max_c: -40..45, precip_mm: 0..500, wind_ms: 0..60, thunderstorm: bool}` — required when `weather_known` is true, **omitted otherwise**; unknown fields are rejected and `temp_min_c` must not exceed `temp_max_c`. `weather_known: bool` (default true). |
| Output schema | `{risk_score: int 0..100, band: "ok"\|"caution"\|"no_go", factors: [{rule, contribution, detail}]}`. Bands: <35 `ok`, 35–69 `caution`, ≥70 `no_go`. Score is the capped sum of contributions. |
| Heuristics | `thunderstorm_on_exposed_ridge` +70 / `_on_mixed` +30 / `_on_sheltered` +10 (exposure-scaled); `severe_wind_any_terrain` +50 (wind ≥25, any terrain); `extreme_cold_any_terrain` +50 (temp_min < −20, any terrain); `high_wind_on_open_terrain` +30/+45 (wind ≥15/≥20, halved on mixed); `wet_exposed_ridge` +35 (precip ≥10 on exposed ridge above 1800 m); `swollen_river_crossings` +10 per crossing, cap 30 (precip ≥10); `heavy_precipitation` +20 (precip ≥20); `cold_at_altitude` +15/+30 (temp_min < −5 above 1800 m); `weather_unknown` +40. |
| Error conditions | `UNKNOWN_SEGMENT`; `MISSING_WEATHER` (weather omitted while `weather_known` is true); `CONTRADICTORY_WEATHER` (a weather object supplied while `weather_known` is false — refused rather than silently discarded, which would return a *lower* score for severe conditions); schema rejection for out-of-range or inverted values. |
| Side effects | None. |
| Example | Input `["CH-001"]` with `thunderstorm:true` and otherwise calm weather → `{risk_score: 70, band: "no_go", factors:[{rule:"thunderstorm_on_exposed_ridge", contribution:70, ...}]}`. A thunderstorm on an exposed 2000 m ridge clears the threshold on its own. |

### 3. `suggest_alternative_segments`

| Element | Content |
|---|---|
| Name | `suggest_alternative_segments` |
| Purpose | Find connected, constraint-satisfying alternative segment chains for one rejected day. Used by replanner subagents. |
| Model-facing description | "Suggest alternative trail-segment chains for one hiking day. Given the day's start node, target end node (or 'flexible' to allow any shelter), and constraints (max distance, max ascent, max exposure level), searches the trail graph and returns up to k ranked feasible alternatives with full segment attributes. Returns an empty candidate list if nothing satisfies the constraints." |
| Input schema | `start_node: string`; `end_node: string \| "flexible"`; `constraints: {max_km: 0<..40, max_ascent_m: 0<..3000, max_exposure: "sheltered"\|"mixed"\|"exposed_ridge"}`; `k: int 1..5` (default 3). |
| Output schema | `{status: "ok", candidates: [{segments, total_km, total_ascent_m, end_node, ends_at_shelter, detour_score}]}`, ranked by `detour_score` ascending (`km + ascent/200 + 5 if no shelter`). |
| Search bounds | Up to `MAX_PATHS_SCANNED` (20) simple paths are enumerated per target and the first `k` that satisfy the constraints are returned. The scan bound is deliberately larger than `k`: distance is monotone in path order so the scan can stop early on `max_km`, but **ascent is not** — a longer-but-flatter route must still be reachable. With `end_node:"flexible"` the search fans out over all shelter nodes, so cost grows with the number of shelters (≈10 ms on this 9-shelter dataset; it would be seconds on a much larger one). |
| Error conditions | `INVALID_NODE` (unknown start or end node); schema rejection for out-of-range `k` or constraints. Empty `candidates` with `status:"ok"` is **not** an error. |
| Side effects | None. The NetworkX graph is built once at server startup from `trails.geojson` (~0.6 ms) and reused. |
| Example | `start_node:"NESAMOVYTE", end_node:"BYSTRETS", constraints:{max_km:18, max_ascent_m:1100, max_exposure:"mixed"}` → best candidate `["CH-022","CH-014"]`, 9.3 km, `ends_at_shelter: true` — the valley bypass of the Turkul ridge. |

### 4. `estimate_logistics`

| Element | Content |
|---|---|
| Name | `estimate_logistics` |
| Purpose | Produce a logistics sheet for an accepted itinerary: hiking time, daylight margin, water availability and food-days. Called once at the end of the workflow. |
| Model-facing description | "Estimate logistics for an accepted itinerary: per-day hiking time (Naismith's rule), daylight margin for the given dates, water-source availability, and food-days. Call this only after the itinerary has been validated; pass the normalized_itinerary returned by validate_itinerary." |
| Input schema | `normalized_itinerary: {days: [{date, segments, total_km, total_ascent_m, ends_at_shelter}]}` — 1–7 days, `date` uses the same strict pattern as `validate_itinerary`, `total_km` 0..200, `total_ascent_m` 0..20000; extra keys from the normalized day (`start_node`, `max_altitude_m`, …) are accepted and ignored. `party` as above, but **only `party.size` is used** — `fitness` and `has_tent` are required by the shared `Party` model and ignored here. |
| Output schema | `{days: [{day, date, hiking_hours, daylight_hours, daylight_margin_hours, water_sources, ends_at_shelter}], food_days: int, party_size: int, water_warnings: [string]}` |
| Computation (and its crudeness) | `hiking_hours` = Naismith's rule: `total_km/4 + total_ascent_m/600`. **`total_km` and `total_ascent_m` are recomputed from `segments`, not trusted from the input** — otherwise the two halves of one day sheet could describe different hikes. `daylight_hours` is a coarse three-branch month lookup (16 h for May–Aug, 12 h for Apr/Sep/Oct, 9 h otherwise) — no latitude, no day-within-month, and it ignores the `Sunrise`/`Sunset` values the weather tool actually supplies. `water_sources` is the count of `river_crossings` on the day's segments — a crossing is treated as a refill point. `water_warnings` fires when a day has zero crossings and exceeds 10 km (an arbitrary threshold). `food_days` is simply `len(days)`, not scaled by party size. `daylight_margin_hours` may go negative without a warning. |
| Error conditions | `UNKNOWN_SEGMENT`; `DISCONNECTED_DAY` (a day's segments do not chain — re-run `validate_itinerary` first); schema rejection for a malformed date, out-of-range totals, or >7 days. Note: this tool cannot see `status`, so it will happily cost an itinerary that `validate_itinerary` rejected on a *hard* violation — the caller is responsible for the gate. |
| Side effects | None (read-only over the local dataset). |
| Example | Input: normalized 2-day August plan (6.6 km/790 m, 9.3 km/230 m) + party of 4 → day 1 `hiking_hours: 3.0, daylight_hours: 16, daylight_margin_hours: 13.0, water_sources: 0`, `food_days: 2`, and a water warning for any waterless day over 10 km. |

### Agent-side helper (not one of the four tools)

`parse_weather_text` is exposed by an **in-process** SDK MCP server named
`agent_local` (`agent/helpers_server.py`), not by the custom server. It exists
because the subagent must be able to *invoke* the parser, so it has to be a tool
rather than plain Python — but it belongs to the agent, not to the trailsmith
domain server, and it does **not** count toward Part B's three-tool requirement.

- **Input**: `text` (the full raw `weather` output), `target_date` (`YYYY-MM-DD`).
- **Output**: `{status:"ok", weather:{temp_min_c, temp_max_c, precip_mm, wind_ms, thunderstorm}, excerpt:"<short bounded quote of the source values>"}`, or `{status:"error", code, message}`.
- **Error codes**: `NO_FORECAST_FOR_DATE` (date outside the 5-day window, or unrecognized output — this is also how the upstream server's degenerate all-zeros response is caught), `MISSING_WIND`, `IMPLAUSIBLE_VALUE` (value outside a physical range).
- `excerpt` is built from the parser's own matched groups rather than letting the model copy arbitrary text out of the tool output, which keeps the value trace bounded.

---

## Existing server: OpenWeather MCP (`mschneider82/mcp-openweather`) — Part A

Contract as observed in this project's configuration (binary built with
`go install github.com/mschneider82/mcp-openweather@latest`, wired as MCP server
`weather` with `OWM_API_KEY` passed from `.env`):

| Element | Content |
|---|---|
| Name | `weather` (the server's single tool) |
| Purpose in this project | Fetch current conditions plus the 5-day forecast for each itinerary day's settlement; the parsed values feed `assess_segment_risk` and can drive replanning. |
| Model-facing description (exact, as discovered) | "Get current and forecast weather information for a specific City" |
| Input schema (exact, as discovered) | `city: string` (required — "Location to get weather. If location has a space, wrap the location in double quotes."); `units: string` (optional, default `"c"`, celsius\|fahrenheit\|kelvin); `lang: string` (optional, default `"en"`) |
| Output | Human-readable **text**, not JSON: a current-conditions block (Conditions, Now/High/Low, Pressure, Humidity, FeelsLike, Wind Speed, Wind Degree, Sunrise/Sunset unixtime) followed by "Weather Forecast for &lt;city&gt;" entries in 3-hour steps (`Date & Time`, `Conditions`, `Temp`, `High`, `Low`). **The forecast entries carry no wind and no precipitation** — wind comes only from the current-conditions block, so one value serves as the proxy for every day, and precipitation is estimated from condition keywords. Both limitations are stated in `docs/design_rationale.md`. |
| Error conditions (observed, verified live) | This server version **swallows API errors**: a missing/invalid `OWM_API_KEY` (HTTP 401) *and* an unknown city both return `is_error = false` with a degenerate all-zeros body and an empty city name ("Current weather for :", all values 0, no forecast entries). Our parser rejects that text with `NO_FORECAST_FOR_DATE`, so the failure is detected at the parsing boundary rather than via `is_error`, and the day is assessed conservatively with `weather_known=false`. A network failure/timeout surfaces as a genuine MCP tool error. |
| Side effects | One outbound HTTPS request to api.openweathermap.org per call; no local state. |
| Example | Input `{"city": "Vorokhta,UA", "units": "c", "lang": "en"}` → text starting "Current weather for Vorokhta:" with `Wind Speed: 2.91`, followed by ~40 forecast entries covering 5 days. |

**Why this server:** weather is the load-bearing external signal of the domain — a
thunderstorm forecast on the day the route crosses the Chornohora ridge changes
the plan. The forecast result feeds the risk score, the risk band gates
replanning, and replanning changes the final itinerary.
