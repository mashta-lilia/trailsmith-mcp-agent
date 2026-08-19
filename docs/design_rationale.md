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
chain is ~7 tool-call depths versus ~16 for a linear agent; batch width adds
no wall-clock cost.

The deterministic weather-text parser is exposed to subagents as an in-process
SDK tool (`helpers` server). It is an implementation helper, not one of the
three substantive custom tools, and it keeps forecast extraction testable and
non-hallucinated.

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
  meteorologically authoritative.
- **Replanning explores bounded alternatives** (3 paths per target, one
  relaxation round) to keep runs short; a rejected plan may still have exotic
  feasible routes outside the search bound.

## Governance note

Task routing between the main agent and subagents follows the Agent
Orchestrator pattern: the main agent owns sequential gates (validation, merge,
logistics, synthesis) and delegates only parallelizable, single-day work to
subagents with minimal tool allowlists.
