# Tool-contract documentation

## Common error schema (custom server)

All `trailsmith` tools return errors as MCP tool errors (`is_error = true`)
whose text is a JSON object: `{"error_code": string, "message": string,
"detail": object}`. Example:

```json
{"error_code": "UNKNOWN_SEGMENT", "message": "Segment CH-999 not found in dataset", "detail": {"segment_ids": ["CH-999"]}}
```

An error is always distinguishable from a successful empty result: e.g.
`suggest_alternative_segments` with impossible constraints returns a normal
result `{"status": "ok", "candidates": []}`, not an error.

---

## Custom server: `trailsmith` (Part B / Part C)

### 1. `validate_itinerary`

| Element | Content |
|---|---|
| Name | `validate_itinerary` |
| Purpose | Validate a proposed multi-day itinerary against the trail dataset and party constraints. The model calls it first, before any risk assessment or replanning, and again after merging replanned days. |
| Model-facing description | "Validate a multi-day Carpathian hiking itinerary. Checks that trail segments exist and connect, daily distance and ascent respect the party's fitness caps, and each night ends at a shelter or legal camp (or the party carries a tent). Returns a normalized itinerary and a list of violations. Call this before assessing risk or suggesting alternatives." |
| Input schema | `itinerary: {days: [{date: string (YYYY-MM-DD), segments: [string ^CH-\d{3}$], min 1}] , 1..7 days}` (required); `party: {fitness: "low"\|"moderate"\|"high", size: int 1..12, has_tent: bool}` (required) |
| Output schema | `{status: "ok"\|"invalid", normalized_itinerary: {days: [{date, segments, start_node, end_node, end_settlement, total_km, total_ascent_m, max_altitude_m, ends_at_shelter}]}, violations: [{code, severity: "hard"\|"soft", day, message}]}`. `status:"invalid"` is a *successful* validation that found problems. Violation codes: `DISCONNECTED_DAY`, `DAY_BOUNDARY_GAP` (hard), `DAILY_DISTANCE_EXCEEDED`, `DAILY_ASCENT_EXCEEDED` (soft), `NO_SHELTER_AT_NIGHT` (hard). |
| Error conditions | `UNKNOWN_SEGMENT` (segment ID not in dataset); pydantic schema rejection for malformed input (wrong pattern, >7 days). |
| Side effects | None (read-only over the local dataset). |
| Example | Input: 1 day `["CH-005","CH-004"]`, party moderate/4/tent → Output: `status:"ok"`, day normalized to start ZAROSLYAK, end NESAMOVYTE, 6.6 km, 790 m ascent, `ends_at_shelter: true`, `violations: []`. |

### 2. `assess_segment_risk`

| Element | Content |
|---|---|
| Name | `assess_segment_risk` |
| Purpose | Compute a weather-conditioned risk score for one day's segments. Called by day-assessor subagents after they parse a forecast, and by replanner subagents to re-score candidates. |
| Model-facing description | "Assess the risk of hiking a given day's trail segments under a provided weather summary. Combines segment exposure, altitude, and river crossings with wind, precipitation, temperature, and thunderstorm risk using explicit mountain-safety heuristics. Returns a 0-100 risk score, a band (ok/caution/no_go), and itemized factors. When no reliable forecast is available, set weather_known=false and omit weather." |
| Input schema | `segments: [string]` (required, min 1); `weather: {temp_min_c: -40..45, temp_max_c: -40..45, precip_mm: 0..500, wind_ms: 0..60, thunderstorm: bool}` (required when `weather_known` is true, omitted otherwise); `weather_known: bool` (optional, default true) |
| Output schema | `{risk_score: int 0..100, band: "ok"\|"caution"\|"no_go", factors: [{rule, contribution, detail}]}`. Bands: <35 ok, 35-69 caution, >=70 no_go. Rules: `thunderstorm_on_exposed_ridge` (+60), `high_wind_on_open_terrain` (+30/45, halved on mixed terrain), `swollen_river_crossings` (+10/crossing, cap 30), `heavy_precipitation` (+20), `cold_at_altitude` (+15/30), `weather_unknown` (+40 → caution). |
| Error conditions | `UNKNOWN_SEGMENT`; `MISSING_WEATHER` (weather omitted while weather_known=true); pydantic rejection for out-of-range weather values. |
| Side effects | None. |
| Example | Input: `["CH-001"]`, weather `wind_ms:18, thunderstorm:true, precip_mm:20` → Output: `risk_score: 90+, band: "no_go"`, factors include `thunderstorm_on_exposed_ridge` (+60). |

### 3. `suggest_alternative_segments`

| Element | Content |
|---|---|
| Name | `suggest_alternative_segments` |
| Purpose | Find connected, constraint-satisfying alternative segment chains for one rejected day. Used by replanner subagents. |
| Model-facing description | "Suggest alternative trail-segment chains for one hiking day. Given the day's start node, target end node (or 'flexible' to allow any shelter), and constraints (max distance, max ascent, max exposure level), searches the trail graph and returns up to k ranked feasible alternatives with full segment attributes. Returns an empty candidate list if nothing satisfies the constraints." |
| Input schema | `start_node: string` (required); `end_node: string \| "flexible"` (required); `constraints: {max_km: 0<..40, max_ascent_m: 0<..3000, max_exposure: "sheltered"\|"mixed"\|"exposed_ridge"}` (required); `k: int 1..5` (default 3) |
| Output schema | `{status: "ok", candidates: [{segments: [string], total_km, total_ascent_m, end_node, ends_at_shelter, detour_score}]}` ranked by `detour_score` ascending (km + ascent/200 + 5 if no shelter). Empty `candidates` = no feasible route, not an error. |
| Error conditions | `INVALID_NODE` (unknown start or end node). |
| Side effects | None. Implementation: NetworkX graph built once at startup; `shortest_simple_paths` bounded by the exposure filter and post-filtered by km/ascent. |
| Example | Input: `start_node:"ZAROSLYAK", end_node:"NESAMOVYTE", constraints:{max_km:14, max_ascent_m:900, max_exposure:"sheltered"}` → Output: best candidate `["CH-006","CH-007"]` (Prut valley forest route), `ends_at_shelter: true`. |

### 4. `estimate_logistics`

| Element | Content |
|---|---|
| Name | `estimate_logistics` |
| Purpose | Produce a logistics sheet for the accepted itinerary (hiking time, daylight margin, water, food-days). Called once at the end of the workflow. |
| Model-facing description | "Estimate logistics for an accepted itinerary: per-day hiking time (Naismith's rule), daylight margin for the given dates, water-source availability, and food-days. Call this only after the itinerary has been validated; pass the normalized_itinerary returned by validate_itinerary." |
| Input schema | `normalized_itinerary: object` — must be the object returned by `validate_itinerary` (days need `date`, `segments`, `total_km`, `total_ascent_m`, `ends_at_shelter`); `party` as above |
| Output schema | `{days: [{day, date, hiking_hours, daylight_hours, daylight_margin_hours, water_sources, ends_at_shelter}], food_days: int, party_size: int, water_warnings: [string]}` |
| Error conditions | `NOT_NORMALIZED` (input is not a validate_itinerary result); `UNKNOWN_SEGMENT`. |
| Side effects | None. |
| Example | Input: normalized 2-day plan + moderate party → Output: day 1 `hiking_hours: 3.0, daylight_hours: 12, daylight_margin_hours: 9.0`, `food_days: 2`. |

---

## Existing server: OpenWeather MCP (`mschneider82/mcp-openweather`) — Part A

Contract as observed in this project's configuration (binary built with
`go install github.com/mschneider82/mcp-openweather@latest`, wired as MCP
server `weather` with `OWM_API_KEY` passed from `.env`):

| Element | Content |
|---|---|
| Name | `weather` (the server's single tool) |
| Purpose in this project | Fetch current conditions plus the 5-day forecast for each itinerary day's settlement; the parsed values feed `assess_segment_risk` and drive replanning. |
| Model-facing description (exact, as discovered) | "Get current and forecast weather information for a specific City" |
| Input schema (exact, as discovered) | `city: string` (required — "Location to get weather. If location has a space, wrap the location in double quotes."); `units: string` (optional, default `"c"`, celsius\|fahrenheit\|kelvin); `lang: string` (optional, default `"en"`) |
| Output | Human-readable **text**, not JSON: a current-conditions block (Conditions, Now/High/Low temperatures, Pressure, Humidity, FeelsLike, Wind Speed, Wind Degree, Sunrise/Sunset unixtime) followed by "Weather Forecast for <city>" entries in 3-hour steps (`Date & Time`, `Conditions`, `Temp`, `High`, `Low`). Our `agent/weather_parser.py` converts this text into the structured summary; that parsing step is part of the demonstrated value trace. |
| Error conditions (observed) | The server (v1.0.0) **swallows API errors**: both a missing/invalid `OWM_API_KEY` (HTTP 401) and an unknown city return `is_error=false` with a degenerate all-zeros response and an empty city name ("Current weather for :", all values 0, no forecast entries) — verified live. Our parser rejects that text (`NO_FORECAST_FOR_DATE`) and the day is assessed conservatively as weather-unknown, so the failure is detected at the parsing boundary rather than via `is_error`. A network failure/timeout surfaces as a genuine MCP tool error. |
| Side effects | One outbound HTTPS request to api.openweathermap.org per call; no local state. |
| Example | Input: `{"city": "Vorokhta,UA", "units": "c", "lang": "en"}` → Output: text starting "Current weather for Vorokhta..." followed by ~40 forecast entries covering 5 days. |

**Why this server:** weather is the load-bearing external signal of the
domain — a thunderstorm forecast on the day the route crosses the Chornohora
ridge must change the plan. The forecast result demonstrably affects later
steps (risk band → replanning → final itinerary), satisfying Part A's
integration requirement.
