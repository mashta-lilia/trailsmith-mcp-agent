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

`vorokhta_ua.txt` is the genuine `../openweather/vorokhta_ua.txt` recording with
the `Conditions:` lines for **2026-08-21 only** replaced by
`Thunderstorm thunderstorm with heavy rain`. Every other line — timestamps,
temperatures, wind, pressure, all other dates, and all other settlements — is
the unmodified genuine text.

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
