# SYNTHETIC scenario fixtures — not recorded API responses

**These files are NOT genuine OpenWeather recordings.** The genuine recordings
live in `../openweather/` and are what the replay mode uses by default.

## Why this exists

`no_go` risk (and therefore the replanning branch of the agent workflow) is
weather-driven by design: it requires a thunderstorm or severe conditions on
exposed terrain. Carpathian forecasts are frequently calm, so on a calm day the
agent correctly reports `ok`/`caution` and never replans. That is right
behaviour, but it means the replanning half of the workflow cannot be
demonstrated on demand.

This directory provides a **deliberately modified input** so that branch can be
exercised reproducibly.

## How it was made

Each file is the corresponding genuine recording from `../openweather/` with the
`Conditions:` lines for **2026-08-21 only** replaced by
`Thunderstorm thunderstorm with heavy rain`. Every other line — timestamps,
temperatures, wind, pressure, and every other date — is the unmodified genuine
text.

The substitution is applied to **all four settlements**, not just one. A day is
assessed using the settlement its route ends at (day 2 of
`demo/itinerary_storm.json` ends at BYSTRETS, which maps to `Verkhovyna,UA`), so
storming a single city would leave the scenario dependent on which settlement a
day happens to map to. A convective system covering the whole range on one day is
also the meteorologically coherent version of this scenario.

Regenerate it after re-recording fixtures or changing demo dates — the
substitution is pinned to a specific date.

## Why this is not a hard-coded answer

The file is an **input**, not an output. It flows through the identical path as
a live response: the same regex parser, the same range validation, the same
`assess_segment_risk` heuristics, the same replanning search. Nothing here
contains a risk score, a band, a chosen route, or any part of the final report —
all of that is genuinely computed from this input. Deleting this directory
changes which branch runs, not whether the code works.

## Usage

```
$env:REPLAY=1
$env:FIXTURE_SET="scenario_storm"
.venv\Scripts\python -m agent.runner demo\itinerary_storm.json
```

Omit `FIXTURE_SET` to replay the genuine recordings instead.

**Disclose this during the defence.** State plainly that the storm scenario uses
a modified condition line because no thunderstorm was forecast at recording
time, and that the genuine recordings are used for every other demo.
