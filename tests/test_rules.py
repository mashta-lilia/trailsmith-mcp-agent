import pytest

from trailsmith_mcp.dataset import get_dataset
from trailsmith_mcp import rules

DATASET = get_dataset()
PARTY_MODERATE = {"fitness": "moderate", "size": 4, "has_tent": True}
PARTY_LOW_NO_TENT = {"fitness": "low", "size": 2, "has_tent": False}


def make_itinerary(days: list[list[str]]) -> dict:
    return {
        "days": [
            {"date": f"2026-09-{12 + i:02d}", "segments": segs}
            for i, segs in enumerate(days)
        ]
    }


class TestValidateItinerary:
    def test_clean_traverse_passes(self):
        itinerary = make_itinerary([
            ["CH-005", "CH-004"],          # Zaroslyak -> Pozhyzhevska -> Nesamovyte
            ["CH-008", "CH-009", "CH-014"],  # ridge to Shpytsi, down to Bystrets
            ["CH-015"],                     # Bystrets -> Dzembronia
        ])
        result = rules.validate_itinerary(DATASET, itinerary, PARTY_MODERATE)
        assert result["status"] == "ok"
        assert result["violations"] == []
        day1 = result["normalized_itinerary"]["days"][0]
        assert day1["start_node"] == "ZAROSLYAK"
        assert day1["end_node"] == "NESAMOVYTE"
        assert day1["end_settlement"] == "Vorokhta"

    def test_disconnected_day_is_hard_violation(self):
        itinerary = make_itinerary([["CH-001", "CH-013"]])
        result = rules.validate_itinerary(DATASET, itinerary, PARTY_MODERATE)
        codes = [v["code"] for v in result["violations"]]
        assert "DISCONNECTED_DAY" in codes

    def test_day_boundary_gap_detected(self):
        itinerary = make_itinerary([
            ["CH-005", "CH-004"],  # ends at NESAMOVYTE
            ["CH-015"],            # starts at BYSTRETS - gap
        ])
        result = rules.validate_itinerary(DATASET, itinerary, PARTY_MODERATE)
        codes = [v["code"] for v in result["violations"]]
        assert "DAY_BOUNDARY_GAP" in codes

    def test_low_fitness_flags_ascent(self):
        itinerary = make_itinerary([["CH-001"], ["CH-002", "CH-003", "CH-004"]])
        result = rules.validate_itinerary(DATASET, itinerary, PARTY_LOW_NO_TENT)
        codes = [v["code"] for v in result["violations"]]
        assert "DAILY_ASCENT_EXCEEDED" in codes  # 850 m > 700 m cap

    def test_no_shelter_without_tent(self):
        # Day ends on Hoverla summit (no shelter), party has no tent.
        itinerary = make_itinerary([["CH-005", "CH-003", "CH-002"], ["CH-017"]])
        result = rules.validate_itinerary(DATASET, itinerary, PARTY_LOW_NO_TENT)
        codes = [v["code"] for v in result["violations"]]
        assert "NO_SHELTER_AT_NIGHT" in codes

    def test_single_segment_day_is_oriented_by_previous_day(self):
        itinerary = make_itinerary([["CH-005"], ["CH-004"]])
        result = rules.validate_itinerary(DATASET, itinerary, PARTY_MODERATE)
        codes = [v["code"] for v in result["violations"]]
        assert "DAY_BOUNDARY_GAP" not in codes


class TestAssessRisk:
    CALM = {"temp_min_c": 8, "temp_max_c": 16, "precip_mm": 0, "wind_ms": 4,
            "thunderstorm": False}

    def test_calm_weather_on_ridge_is_ok(self):
        result = rules.assess_risk(DATASET, ["CH-001"], self.CALM)
        assert result["band"] == "ok"
        assert result["risk_score"] < 35

    def test_thunderstorm_on_ridge_is_no_go(self):
        stormy = {**self.CALM, "thunderstorm": True, "wind_ms": 18, "precip_mm": 20}
        result = rules.assess_risk(DATASET, ["CH-001"], stormy)
        assert result["band"] == "no_go"
        rules_hit = {f["rule"] for f in result["factors"]}
        assert "thunderstorm_on_exposed_ridge" in rules_hit

    def test_thunderstorm_in_sheltered_forest_is_not_no_go(self):
        stormy = {**self.CALM, "thunderstorm": True, "precip_mm": 12}
        result = rules.assess_risk(DATASET, ["CH-006"], stormy)
        assert result["band"] != "no_go"

    def test_rain_with_crossings_adds_factor(self):
        wet = {**self.CALM, "precip_mm": 15}
        result = rules.assess_risk(DATASET, ["CH-013"], wet)
        rules_hit = {f["rule"] for f in result["factors"]}
        assert "swollen_river_crossings" in rules_hit

    def test_weather_unknown_forces_caution(self):
        result = rules.assess_risk(DATASET, ["CH-001"], self.CALM, weather_known=False)
        assert result["band"] == "caution"
        assert result["factors"][0]["rule"] == "weather_unknown"


class TestSuggestAlternatives:
    def test_sheltered_alternative_avoids_ridge(self):
        candidates = rules.suggest_alternatives(
            DATASET, "ZAROSLYAK", "NESAMOVYTE",
            {"max_km": 14, "max_ascent_m": 900, "max_exposure": "sheltered"}, 3,
        )
        assert candidates, "expected at least one sheltered route"
        best = candidates[0]
        assert best["segments"] == ["CH-006", "CH-007"]
        assert best["ends_at_shelter"] is True

    def test_impossible_constraints_return_empty_not_error(self):
        candidates = rules.suggest_alternatives(
            DATASET, "ZAROSLYAK", "POP_IVAN",
            {"max_km": 5, "max_ascent_m": 300, "max_exposure": "sheltered"}, 3,
        )
        assert candidates == []

    def test_flexible_end_finds_shelter(self):
        candidates = rules.suggest_alternatives(
            DATASET, "SHPYTSI", "flexible",
            {"max_km": 12, "max_ascent_m": 600, "max_exposure": "sheltered"}, 3,
        )
        assert candidates
        assert all(c["ends_at_shelter"] for c in candidates)


class TestServerErrorContracts:
    """Errors must surface as structured {error_code,...} JSON, never raw tracebacks."""

    @staticmethod
    def _call(tool: str, args: dict):
        import asyncio
        from mcp.client import Client
        from trailsmith_mcp.server import server

        async def run():
            async with Client(server) as client:
                return await client.call_tool(tool, args)
        return asyncio.run(run())

    def test_malformed_date_returns_structured_error(self):
        result = self._call("estimate_logistics", {
            "normalized_itinerary": {"days": [{
                "date": "garbage", "segments": ["CH-005"], "total_km": 5.0,
                "total_ascent_m": 100, "ends_at_shelter": True}]},
            "party": {"fitness": "moderate", "size": 2, "has_tent": True},
        })
        assert result.is_error
        assert "NOT_NORMALIZED" in result.content[0].text

    def test_missing_weather_returns_structured_error(self):
        result = self._call("assess_segment_risk", {"segments": ["CH-001"]})
        assert result.is_error
        assert "MISSING_WEATHER" in result.content[0].text

    def test_weather_unknown_without_weather_succeeds(self):
        result = self._call("assess_segment_risk", {
            "segments": ["CH-001"], "weather_known": False})
        assert not result.is_error
        assert result.structured_content["band"] == "caution"


class TestEstimateLogistics:
    def test_logistics_for_validated_plan(self):
        itinerary = make_itinerary([["CH-005", "CH-004"], ["CH-022", "CH-014"]])
        validated = rules.validate_itinerary(DATASET, itinerary, PARTY_MODERATE)
        assert validated["status"] == "ok"
        result = rules.estimate_logistics(
            DATASET, validated["normalized_itinerary"]["days"], PARTY_MODERATE
        )
        assert result["food_days"] == 2
        assert all(d["daylight_margin_hours"] > 0 for d in result["days"])
        september_day = result["days"][0]
        assert september_day["daylight_hours"] == 12
