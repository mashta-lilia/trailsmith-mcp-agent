# Design rationale

## Why the existing server (OpenWeather MCP) is relevant

TrailSmith plans multi-day Carpathian hikes, where weather volatility is the
dominant safety variable. The `weather` tool provides the live 5-day forecast
per settlement; its values are parsed and fed into `assess_segment_risk`, so a
stormy forecast measurably changes the plan (a ridge day is replanned onto a
sheltered valley route). The connection is load-bearing, not decorative.

## Why each custom tool belongs at the MCP boundary

- **`validate_itinerary`** encodes dataset access plus graph-connectivity and
  fitness rules that must be deterministic and identical for every caller.
  Leaving this to the model would make validation probabilistic; keeping it in
  the agent process would break process separation and reuse.
- **`assess_segment_risk`** is the bridge between two data domains (trail
  attributes x weather). The heuristics are explicit domain constants that
  must be auditable and reproducible during the defence — an MCP tool with a
  constrained schema guarantees the same inputs always produce the same score.
- **`suggest_alternative_segments`** requires graph search over the full
  dataset (NetworkX, built at server startup). The dataset lives with the
  server, so the search must too; the constrained contract (max km/ascent/
  exposure) keeps the model from requesting unbounded computation.
- **`estimate_logistics`** derives hiking time, daylight margins, and water
  data from dataset attributes; same locality argument.

## How the tool set supports the workflow

validate → (per day) fetch+parse+assess → replan failing days → re-validate →
logistics → report. Every tool's output is consumed by a later step:
`normalized_itinerary` feeds the day-assessors and `estimate_logistics`; risk
bands gate replanning; candidate lists are re-scored and merged; the final
validation closes the loop.

## Subagent design

Two subagent types (Claude Agent SDK `agents`):

- **day-assessor** (one per day, spawned in parallel): `weather` +
  `parse_weather_text` + `assess_segment_risk`. Fetching and scoring are fused
  in one subagent because scoring strictly depends on that day's forecast — a
  separate risk subagent would add hand-off overhead with no parallelism gain.
- **replanner** (one per no_go day, spawned in parallel):
  `suggest_alternative_segments` + `assess_segment_risk`, with a bounded
  relaxation heuristic (exposure down one level, then +20% distance).

Speed-up: for a 4-day itinerary with 2 failing days, the longest sequential
chain is ~7 tool-call depths versus ~16 for a linear agent; batch width adds no
wall-clock cost. This is a call-depth count derived from the workflow, not a
measured wall-clock benchmark — the parallel batch width is chosen by the CLI's
Task scheduler, so actual speed-up varies.

The deterministic weather-text parser is exposed to subagents as an in-process
SDK tool (the `agent_local` server). It is an implementation helper, not one of the
four custom tools, and it keeps forecast extraction testable and
non-hallucinated. It is registered under the name `agent_local` so a reader of
the run log cannot mistake it for the custom server; a run therefore shows three
MCP connections, of which two are the assignment's.

## Trade-offs and known limitations

- **Curated dataset vs. live OSM pipeline:** the dataset is manually curated
  (see `data/PROVENANCE.md`) with simplified straight-line geometry. This
  keeps the demo deterministic and reviewable; the cost is coarse attribute
  precision. Attributes, not geometry, drive all tool logic.
- **Text parsing of the weather tool:** the upstream server returns prose, so
  precipitation must be estimated from condition keywords and wind comes from
  the current-conditions block only. The parser range-validates everything and
  fails closed into the conservative weather-unknown path.
- **Silent upstream failure:** with a missing API key the upstream server
  returns an all-zeros response instead of an error. We detect it in parsing
  (no forecast entries) rather than via `is_error` — documented and shown in
  the failure demo.
- **Risk heuristics are constants,** not a validated safety model; they are
  designed to be explainable, monotone, and easy to defend, not
  meteorologically authoritative. Two calibration decisions are deliberate and
  worth stating: the thunderstorm term is *exposure-scaled* (+70 exposed ridge,
  +30 mixed, +10 sheltered) so that the textbook no-go clears the band threshold
  on its own; and two terms (`severe_wind_any_terrain`, `extreme_cold_any_terrain`)
  are deliberately **not** gated on exposure, because an earlier version scored
  hurricane-force wind at −40 °C in forest as zero.
- **`precip_mm` is a keyword→millimetre guess table**, not a measurement: the
  forecast entries carry no precipitation figure, so a single "rain" token
  becomes a flat 12.0 mm. That sits just above the 10 mm threshold for
  `swollen_river_crossings` and `wet_exposed_ridge`, so those rules are sensitive
  to a fabricated constant. The worst matching keyword wins, so the estimate is
  order-independent.
- **Wind is taken from the current-conditions block for every future day,**
  because forecast entries carry no wind. Day 5's wind is therefore today's wind.
- **`daylight_hours` is a three-branch month lookup**, ignoring latitude and the
  `Sunrise`/`Sunset` values the weather tool actually returns.
- **`estimate_logistics` cannot see validation status,** so it will cost an
  itinerary that was rejected on a hard violation. The workflow gate is the
  caller's responsibility.
- **Replanning explores bounded alternatives** (up to 20 simple paths scanned
  per target, one relaxation round) to keep runs short; a rejected plan may still have exotic
  feasible routes outside the search bound.

## Governance note

Task routing between the main agent and subagents follows the Agent
Orchestrator pattern: the main agent owns sequential gates (validation, merge,
logistics, synthesis) and delegates only parallelizable, single-day work to
subagents with minimal tool allowlists.
