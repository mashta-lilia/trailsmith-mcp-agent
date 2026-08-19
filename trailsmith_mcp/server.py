"""TrailSmith custom MCP server: domain tools over the Carpathian trail dataset."""
from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field, field_validator, model_validator

from .dataset import get_dataset
from . import rules

server = MCPServer(
    name="trailsmith",
    instructions=(
        "Domain tools for planning multi-day hiking itineraries in the "
        "Ukrainian Carpathians (Chornohora area). Validate an itinerary first, "
        "then assess per-day risk with a weather summary, then suggest "
        "alternatives for rejected days."
    ),
)


def tool_error(code: str, message: str, detail: dict[str, Any] | None = None) -> ToolError:
    return ToolError(json.dumps({"error_code": code, "message": message, "detail": detail or {}}))


class DayPlan(BaseModel):
    date: str = Field(description="ISO date (YYYY-MM-DD)", pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")
    segments: list[Annotated[str, Field(pattern=r"^CH-\d{3}$")]] = Field(
        min_length=1, max_length=20,
        description="Ordered trail segment IDs for the day; must be distinct",
    )

    @field_validator("segments")
    @classmethod
    def _distinct(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("segments must not repeat within a day")
        return v


class Itinerary(BaseModel):
    days: list[DayPlan] = Field(min_length=1, max_length=7)


class Party(BaseModel):
    fitness: Literal["low", "moderate", "high"]
    size: int = Field(ge=1, le=12)
    has_tent: bool


class Violation(BaseModel):
    code: str
    severity: Literal["hard", "soft"]
    day: int
    message: str


class AppliedCaps(BaseModel):
    fitness: Literal["low", "moderate", "high"]
    max_km: float
    max_ascent_m: int


class ValidationResult(BaseModel):
    status: Literal["ok", "invalid"]
    normalized_itinerary: dict[str, Any]
    violations: list[Violation]
    applied_caps: AppliedCaps


class NormalizedDay(BaseModel):
    """One day as returned by validate_itinerary. Extra keys are preserved."""

    model_config = {"extra": "allow"}

    date: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")
    segments: list[Annotated[str, Field(pattern=r"^CH-\d{3}$")]] = Field(
        min_length=1, max_length=20
    )
    total_km: float = Field(ge=0, le=200)
    total_ascent_m: int = Field(ge=0, le=20000)
    ends_at_shelter: bool


class NormalizedItinerary(BaseModel):
    days: list[NormalizedDay] = Field(min_length=1, max_length=7)


class WeatherSummary(BaseModel):
    model_config = {"extra": "forbid"}

    temp_min_c: float = Field(ge=-40, le=45)
    temp_max_c: float = Field(ge=-40, le=45)
    precip_mm: float = Field(ge=0, le=500)
    wind_ms: float = Field(ge=0, le=60)
    thunderstorm: bool

    @model_validator(mode="after")
    def _ordered(self) -> "WeatherSummary":
        if self.temp_min_c > self.temp_max_c:
            raise ValueError("temp_min_c must not exceed temp_max_c")
        return self


class RiskFactor(BaseModel):
    rule: str
    contribution: int
    detail: str


class RiskResult(BaseModel):
    risk_score: int = Field(ge=0, le=100)
    band: Literal["ok", "caution", "no_go"]
    factors: list[RiskFactor]


class AlternativeConstraints(BaseModel):
    max_km: float = Field(gt=0, le=40)
    max_ascent_m: int = Field(gt=0, le=3000)
    max_exposure: Literal["sheltered", "mixed", "exposed_ridge"]


class Candidate(BaseModel):
    segments: list[str]
    total_km: float
    total_ascent_m: int
    end_node: str
    ends_at_shelter: bool
    detour_score: float


class AlternativesResult(BaseModel):
    status: Literal["ok"]
    candidates: list[Candidate]


class LogisticsDay(BaseModel):
    day: int
    date: str
    hiking_hours: float
    daylight_hours: int
    daylight_margin_hours: float
    water_sources: int
    ends_at_shelter: bool


class LogisticsResult(BaseModel):
    days: list[LogisticsDay]
    food_days: int
    party_size: int
    water_warnings: list[str]


def _check_segments_exist(segment_ids: list[str]) -> None:
    dataset = get_dataset()
    unknown = [s for s in segment_ids if s not in dataset.segments]
    if unknown:
        raise tool_error(
            "UNKNOWN_SEGMENT",
            f"Segment {unknown[0]} not found in dataset",
            {"segment_ids": unknown},
        )


@server.tool(
    name="validate_itinerary",
    description=(
        "Validate a multi-day Carpathian hiking itinerary. Checks that trail "
        "segments exist and connect, daily distance and ascent respect the "
        "party's fitness caps, and each night ends at a shelter or legal camp "
        "(or the party carries a tent). Returns a normalized itinerary and a "
        "list of violations. Call this before assessing risk or suggesting "
        "alternatives."
    ),
)
def validate_itinerary(itinerary: Itinerary, party: Party) -> ValidationResult:
    for day in itinerary.days:
        _check_segments_exist(day.segments)
    result = rules.validate_itinerary(
        get_dataset(), itinerary.model_dump(), party.model_dump()
    )
    return ValidationResult(**result)


@server.tool(
    name="assess_segment_risk",
    description=(
        "Assess the risk of hiking a given day's trail segments under a "
        "provided weather summary. Combines segment exposure, altitude, and "
        "river crossings with wind, precipitation, temperature, and "
        "thunderstorm risk using explicit mountain-safety heuristics. Returns "
        "a 0-100 risk score, a band (ok/caution/no_go), and itemized factors. "
        "When no reliable forecast is available, set weather_known=false and "
        "omit weather."
    ),
)
def assess_segment_risk(
    segments: Annotated[list[str], Field(min_length=1)],
    weather: WeatherSummary | None = None,
    weather_known: bool = True,
) -> RiskResult:
    _check_segments_exist(segments)
    if weather_known and weather is None:
        raise tool_error(
            "MISSING_WEATHER",
            "weather is required when weather_known is true; pass "
            "weather_known=false when no reliable forecast is available.",
        )
    if not weather_known and weather is not None:
        # Otherwise the flag would discard a supplied severe forecast and return
        # a LOWER score for genuinely dangerous conditions.
        raise tool_error(
            "CONTRADICTORY_WEATHER",
            "weather_known=false must be used with no weather object; omit "
            "weather, or set weather_known=true to score the forecast supplied.",
            {"weather_supplied": True},
        )
    result = rules.assess_risk(
        get_dataset(), segments, weather.model_dump() if weather else {}, weather_known
    )
    return RiskResult(**result)


@server.tool(
    name="suggest_alternative_segments",
    description=(
        "Suggest alternative trail-segment chains for one hiking day. Given "
        "the day's start node, target end node (or 'flexible' to allow any "
        "shelter), and constraints (max distance, max ascent, max exposure "
        "level), searches the trail graph and returns up to k ranked feasible "
        "alternatives with full segment attributes. Returns an empty candidate "
        "list if nothing satisfies the constraints."
    ),
)
def suggest_alternative_segments(
    start_node: str,
    end_node: str,
    constraints: AlternativeConstraints,
    k: int = Field(default=3, ge=1, le=5),
) -> AlternativesResult:
    dataset = get_dataset()
    if start_node not in dataset.nodes:
        raise tool_error("INVALID_NODE", f"Node {start_node} not found in dataset",
                         {"node": start_node})
    if end_node != "flexible" and end_node not in dataset.nodes:
        raise tool_error("INVALID_NODE", f"Node {end_node} not found in dataset",
                         {"node": end_node})
    candidates = rules.suggest_alternatives(
        dataset, start_node, end_node, constraints.model_dump(), k
    )
    return AlternativesResult(status="ok", candidates=[Candidate(**c) for c in candidates])


@server.tool(
    name="estimate_logistics",
    description=(
        "Estimate logistics for an accepted itinerary: per-day hiking time "
        "(Naismith's rule), daylight margin for the given dates, water-source "
        "availability, and food-days. Call this only after the itinerary has "
        "been validated; pass the normalized_itinerary returned by "
        "validate_itinerary."
    ),
)
def estimate_logistics(
    normalized_itinerary: NormalizedItinerary, party: Party
) -> LogisticsResult:
    days = [d.model_dump() for d in normalized_itinerary.days]
    dataset = get_dataset()
    for day in days:
        _check_segments_exist(day["segments"])
        # Recompute rather than trust the caller: a supplied total_km of 0.1 for
        # a real 6.6 km chain would otherwise yield a nonsense day sheet.
        endpoints = dataset.chain_endpoints(day["segments"])
        if endpoints is None:
            raise tool_error(
                "DISCONNECTED_DAY",
                f"Day dated {day['date']} does not form a connected chain; "
                "re-run validate_itinerary and fix the violations first.",
                {"segments": day["segments"]},
            )
        stats = rules.walk_chain(dataset, day["segments"], endpoints[0])
        day["total_km"] = stats["total_km"]
        day["total_ascent_m"] = stats["total_ascent_m"]
    result = rules.estimate_logistics(get_dataset(), days, party.model_dump())
    return LogisticsResult(**result)
