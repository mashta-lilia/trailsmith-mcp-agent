"""Deterministic parser for the OpenWeather MCP `weather` tool's text output.

The tool returns human-readable text (current block + 5-day forecast entries),
not JSON. This module extracts the structured WeatherSummary consumed by the
custom server's assess_segment_risk tool. Precipitation is not reported
numerically by the tool, so it is estimated from condition keywords; wind is
only present in the current-conditions block, so it serves as the day's proxy.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

TEMP_RANGE = (-40.0, 45.0)
WIND_RANGE = (0.0, 60.0)

# Worst-condition keyword -> estimated precipitation in mm per day.
PRECIP_ESTIMATES: list[tuple[str, float]] = [
    ("thunderstorm", 20.0),
    ("heavy rain", 25.0),
    ("shower", 18.0),
    ("snow", 15.0),
    ("rain", 12.0),
    ("drizzle", 4.0),
]

# Anchored to single lines: an entry missing a field must fail to match rather
# than run on into the next entry and silently borrow its temperatures.
_ENTRY_RE = re.compile(
    r"Date & Time:\s*(?P<date>\d{4}-\d{2}-\d{2})[^\n]*\n\s*"
    r"Conditions:\s*(?P<conditions>[^\n]*)\n\s*"
    r"Temp:\s*(?P<temp>-?\d+(?:\.\d+)?)[^\n]*\n\s*"
    r"High:\s*(?P<high>-?\d+(?:\.\d+)?)[^\n]*\n\s*"
    r"Low:\s*(?P<low>-?\d+(?:\.\d+)?)"
)
_WIND_RE = re.compile(r"Wind Speed:\s*(-?\d+(?:\.\d+)?)")


class WeatherParseError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class WeatherSummary:
    temp_min_c: float
    temp_max_c: float
    precip_mm: float
    wind_ms: float
    thunderstorm: bool
    excerpt: str = ""

    def as_dict(self) -> dict[str, float | bool | str]:
        return {
            "temp_min_c": self.temp_min_c,
            "temp_max_c": self.temp_max_c,
            "precip_mm": self.precip_mm,
            "wind_ms": self.wind_ms,
            "thunderstorm": self.thunderstorm,
            "excerpt": self.excerpt,
        }

    def risk_input(self) -> dict[str, float | bool]:
        """The five numeric fields assess_segment_risk accepts (no excerpt)."""
        return {k: v for k, v in self.as_dict().items() if k != "excerpt"}


def _estimate_precip(conditions: list[str]) -> float:
    """Worst matching keyword wins, so the estimate is order-independent."""
    joined = " ".join(conditions).lower()
    matches = [mm for keyword, mm in PRECIP_ESTIMATES if keyword in joined]
    return max(matches) if matches else 0.0


def parse_weather_text(text: str, target_date: str) -> WeatherSummary:
    """Extract a WeatherSummary for `target_date` (YYYY-MM-DD) from tool text.

    Raises WeatherParseError when the text cannot be parsed or values fall
    outside plausible physical ranges - callers must then follow the
    weather-unknown conservative path.
    """
    entries = [m for m in _ENTRY_RE.finditer(text) if m.group("date") == target_date]
    if not entries:
        raise WeatherParseError(
            "NO_FORECAST_FOR_DATE",
            f"No forecast entries found for {target_date} (beyond the 5-day window, "
            "or unrecognized output format).",
        )

    lows = [float(m.group("low")) for m in entries]
    highs = [float(m.group("high")) for m in entries]
    conditions = [m.group("conditions").strip() for m in entries]

    wind_match = _WIND_RE.search(text)
    if wind_match is None:
        raise WeatherParseError(
            "MISSING_WIND", "No 'Wind Speed' field found in the tool output."
        )

    worst = max(entries, key=lambda m: _estimate_precip([m.group("conditions")]))
    precip = _estimate_precip(conditions)
    wind = float(wind_match.group(1))

    # The excerpt must justify the numbers that were actually scored, and say
    # where each came from. Quoting a single 3-hour entry would understate the
    # day's range and leave precipitation unexplained.
    excerpt = (
        f"{target_date}: {len(entries)} forecast entries; "
        f"worst conditions={worst.group('conditions').strip()[:60]!r}; "
        f"day range low={min(lows)} high={max(highs)} (min/max across entries); "
        f"precip={precip} mm (estimated from condition keywords - the forecast "
        f"carries no precipitation figure); "
        f"wind={wind} m/s (from the current-conditions block, which is the only "
        f"wind the tool reports)"
    )

    summary = WeatherSummary(
        temp_min_c=min(lows),
        temp_max_c=max(highs),
        precip_mm=precip,
        wind_ms=wind,
        thunderstorm=any("thunder" in c.lower() for c in conditions),
        excerpt=excerpt,
    )

    for name, value, (lo, hi) in (
        ("temp_min_c", summary.temp_min_c, TEMP_RANGE),
        ("temp_max_c", summary.temp_max_c, TEMP_RANGE),
        ("wind_ms", summary.wind_ms, WIND_RANGE),
    ):
        if not lo <= value <= hi:
            raise WeatherParseError(
                "IMPLAUSIBLE_VALUE", f"{name}={value} is outside the plausible range."
            )
    return summary
