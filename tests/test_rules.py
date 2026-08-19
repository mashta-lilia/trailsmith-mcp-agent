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
        assert day1["end_settlement"] == "Vorokhta,UA"

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
        result = call_tool("estimate_logistics", {
            "normalized_itinerary": {"days": [{
                "date": "garbage", "segments": ["CH-005"], "total_km": 5.0,
                "total_ascent_m": 100, "ends_at_shelter": True}]},
            "party": {"fitness": "moderate", "size": 2, "has_tent": True},
        })
        assert result.is_error
        # A malformed date is now rejected by the NormalizedDay schema, so the
        # error names the offending field instead of the generic code.
        assert "normalized_itinerary" in result.content[0].text

    def test_missing_weather_returns_structured_error(self):
        result = call_tool("assess_segment_risk", {"segments": ["CH-001"]})
        assert result.is_error
        assert "MISSING_WEATHER" in result.content[0].text

    def test_weather_unknown_without_weather_succeeds(self):
        result = call_tool("assess_segment_risk", {
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


class TestOrientationRegressions:
    """A reverse-listed day must be reversed, not just relabelled."""

    def test_reverse_listed_day_is_costed_in_travel_direction(self):
        # Day 2 is listed in the opposite order to the direction of travel.
        itinerary = make_itinerary([
            ["CH-006", "CH-007"],   # ZAROSLYAK -> FOREST_PRUT -> NESAMOVYTE
            ["CH-014", "CH-022"],   # listed BYSTRETS-ward first
        ])
        result = rules.validate_itinerary(DATASET, itinerary, PARTY_MODERATE)
        day2 = result["normalized_itinerary"]["days"][1]
        assert day2["start_node"] == "NESAMOVYTE"
        assert day2["end_node"] == "BYSTRETS", "start == end is physically impossible"
        assert day2["total_ascent_m"] == 230, "must cost the direction actually walked"
        assert day2["end_settlement"] == "Verkhovyna,UA"
        assert day2["segments"] == ["CH-022", "CH-014"], "output reflects costed order"
        assert result["violations"] == []

    def test_repeated_segment_is_disconnected_not_valid(self):
        result = rules.validate_itinerary(
            DATASET, make_itinerary([["CH-005", "CH-005"]]), PARTY_MODERATE
        )
        assert [v["code"] for v in result["violations"]] == ["DISCONNECTED_DAY"]

    def test_chain_endpoints_is_deterministic(self):
        # Same input must never yield two different orientations.
        assert len({DATASET.chain_endpoints(["CH-005", "CH-004"]) for _ in range(50)}) == 1

    def test_non_endpoint_node_raises_instead_of_lying(self):
        with pytest.raises(ValueError, match="not an endpoint"):
            DATASET.segments["CH-014"].other_end("BOGUS")

    def test_applied_caps_are_published(self):
        result = rules.validate_itinerary(
            DATASET, make_itinerary([["CH-005"]]), PARTY_LOW_NO_TENT
        )
        assert result["applied_caps"] == {
            "fitness": "low", "max_km": 12.0, "max_ascent_m": 700,
        }


class TestRiskCalibration:
    CALM = {"temp_min_c": 8, "temp_max_c": 16, "precip_mm": 0, "wind_ms": 4,
            "thunderstorm": False}

    def test_severe_wind_scores_even_on_sheltered_terrain(self):
        result = rules.assess_risk(DATASET, ["CH-006"], {**self.CALM, "wind_ms": 40})
        assert result["factors"], "hurricane-force wind must not score zero"
        assert result["band"] == "caution"

    def test_extreme_cold_scores_even_on_sheltered_terrain(self):
        result = rules.assess_risk(DATASET, ["CH-006"], {**self.CALM, "temp_min_c": -30})
        assert {f["rule"] for f in result["factors"]} >= {"extreme_cold_any_terrain"}

    def test_thunderstorm_alone_on_exposed_ridge_is_no_go(self):
        result = rules.assess_risk(DATASET, ["CH-001"], {**self.CALM, "thunderstorm": True})
        assert result["band"] == "no_go", "textbook no-go must clear the threshold alone"

    def test_thunderstorm_scales_down_with_exposure(self):
        stormy = {**self.CALM, "thunderstorm": True}
        ridge = rules.assess_risk(DATASET, ["CH-001"], stormy)["risk_score"]
        mixed = rules.assess_risk(DATASET, ["CH-004"], stormy)["risk_score"]
        sheltered = rules.assess_risk(DATASET, ["CH-006"], stormy)["risk_score"]
        assert ridge > mixed > sheltered > 0


class TestAlternativeRecall:
    def test_flatter_route_beyond_first_three_is_found(self):
        # Ascent is not monotone in path length: a longer-but-flatter route must
        # still be discovered even if the three shortest paths bust the cap.
        candidates = rules.suggest_alternatives(
            DATASET, "ZAROSLYAK", "POP_IVAN",
            {"max_km": 40, "max_ascent_m": 1600, "max_exposure": "exposed_ridge"}, 3,
        )
        assert len(candidates) >= 3
        assert all(c["total_ascent_m"] <= 1600 for c in candidates)


def call_tool(tool: str, args: dict):
    """Invoke a tool over an in-process MCP client."""
    import asyncio
    from mcp.client import Client
    from trailsmith_mcp.server import server

    async def run():
        async with Client(server) as client:
            return await client.call_tool(tool, args)
    return asyncio.run(run())


class TestContractHardening:
    """Regressions for the contract violations found by adversarial probing."""

    def test_weather_known_false_cannot_launder_severe_weather(self):
        severe = {"temp_min_c": 5, "temp_max_c": 15, "precip_mm": 40,
                  "wind_ms": 55, "thunderstorm": True}
        scored = call_tool("assess_segment_risk",
                            {"segments": ["CH-001"], "weather": severe})
        assert scored.structured_content["band"] == "no_go"
        # Same weather with the flag flipped must be refused, never scored lower.
        laundered = call_tool("assess_segment_risk", {
            "segments": ["CH-001"], "weather": severe, "weather_known": False})
        assert laundered.is_error
        assert "CONTRADICTORY_WEATHER" in laundered.content[0].text

    def test_logistics_recomputes_distance_from_segments(self):
        result = call_tool("estimate_logistics", {
            "normalized_itinerary": {"days": [{
                "date": "2026-07-10", "segments": ["CH-005", "CH-004"],
                "total_km": 0.1, "total_ascent_m": 0, "ends_at_shelter": True}]},
            "party": {"fitness": "moderate", "size": 2, "has_tent": True},
        })
        assert not result.is_error
        # Real chain is 6.6 km / 790 m -> 3.0 h, not the 0.0 h the caller implied.
        assert result.structured_content["days"][0]["hiking_hours"] > 2.5

    def test_impossible_month_is_rejected_at_the_entry_point(self):
        result = call_tool("validate_itinerary", {
            "itinerary": {"days": [{"date": "2026-13-10", "segments": ["CH-005"]}]},
            "party": {"fitness": "moderate", "size": 2, "has_tent": True},
        })
        assert result.is_error, "month 13 must not round-trip into the workflow"

    def test_out_of_order_dates_are_a_violation(self):
        result = rules.validate_itinerary(DATASET, {"days": [
            {"date": "2026-07-20", "segments": ["CH-005"]},
            {"date": "2026-07-11", "segments": ["CH-004"]},
        ]}, PARTY_MODERATE)
        assert "DATE_SEQUENCE_INVALID" in [v["code"] for v in result["violations"]]

    def test_disconnected_day_keeps_full_schema(self):
        result = rules.validate_itinerary(
            DATASET, make_itinerary([["CH-001", "CH-013"]]), PARTY_MODERATE)
        day = result["normalized_itinerary"]["days"][0]
        assert {"total_km", "total_ascent_m", "ends_at_shelter"} <= day.keys()

    def test_same_start_and_end_yields_no_degenerate_candidate(self):
        candidates = rules.suggest_alternatives(
            DATASET, "NESAMOVYTE", "NESAMOVYTE",
            {"max_km": 30, "max_ascent_m": 2000, "max_exposure": "exposed_ridge"}, 5,
        )
        assert all(c["segments"] for c in candidates), "empty 'go nowhere' route"

    def test_inverted_temperature_range_rejected(self):
        result = call_tool("assess_segment_risk", {
            "segments": ["CH-001"],
            "weather": {"temp_min_c": 40, "temp_max_c": 5, "precip_mm": 0,
                        "wind_ms": 2, "thunderstorm": False}})
        assert result.is_error
