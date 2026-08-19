"""Deterministic end-to-end walkthrough of the domain workflow.

Runs the same sequence the agent orchestrates - validate, parse a forecast,
score each day, replan any no_go day, estimate logistics - but calls the domain
rules directly instead of going through an LLM. No Anthropic credentials and no
network access are required, which makes this the fastest way to verify the
project works and to show the workflow when the agent cannot run.

Usage:
    python scripts/walkthrough.py demo/itinerary_clean.json
    python scripts/walkthrough.py demo/itinerary_storm.json --fixtures scenario_storm
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from agent.weather_parser import WeatherParseError, parse_weather_text  # noqa: E402
from trailsmith_mcp import rules  # noqa: E402
from trailsmith_mcp.dataset import get_dataset  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("request_file", help="a demo/*.json request")
    parser.add_argument(
        "--fixtures", default="openweather",
        help="fixture directory under fixtures/ (default: openweather)",
    )
    parser.add_argument("--city", default="vorokhta_ua", help="fixture slug to read")
    args = parser.parse_args()

    dataset = get_dataset()
    request = json.loads(Path(args.request_file).read_text(encoding="utf-8"))
    fixture = REPO_ROOT / "fixtures" / args.fixtures / f"{args.city}.txt"
    if not fixture.exists():
        print(f"No fixture at {fixture}", file=sys.stderr)
        return 2
    forecast_text = fixture.read_text(encoding="utf-8")

    print(f"Request : {args.request_file}")
    print(f"Forecast: fixtures/{args.fixtures}/{args.city}.txt\n")

    # 1. Validate -------------------------------------------------------------
    result = rules.validate_itinerary(dataset, request["itinerary"], request["party"])
    caps = result["applied_caps"]
    print(f"[1] validate_itinerary -> {result['status']}")
    print(f"    caps applied: {caps['fitness']} "
          f"({caps['max_km']} km, {caps['max_ascent_m']} m per day)")
    for violation in result["violations"]:
        print(f"    {violation['severity'].upper():4s} {violation['code']}: {violation['message']}")
    if any(v["severity"] == "hard" for v in result["violations"]):
        print("\nHard violation - the agent stops here and asks for a corrected plan.")
        return 1

    days = result["normalized_itinerary"]["days"]
    final_plan: list[dict] = []

    # 2. Assess each day, 3. replan the failures ------------------------------
    for index, day in enumerate(days, start=1):
        try:
            weather = parse_weather_text(forecast_text, day["date"])
            risk = rules.assess_risk(dataset, day["segments"], weather.risk_input())
            excerpt = weather.excerpt
        except WeatherParseError as exc:
            weather, excerpt = None, f"parse failed: {exc.code}"
            risk = rules.assess_risk(dataset, day["segments"], {}, weather_known=False)

        print(f"\n[2] day {index} {day['date']}  "
              f"{day['start_node']} -> {day['end_node']}  "
              f"({day['total_km']} km, {day['total_ascent_m']} m)")
        print(f"    forecast: {excerpt}")
        print(f"    risk: {risk['risk_score']} {risk['band']}")
        for factor in risk["factors"]:
            print(f"      +{factor['contribution']:<3} {factor['rule']}: {factor['detail']}")

        chosen = day
        if risk["band"] == "no_go" and weather is not None:
            print(f"[3] day {index} is no_go - replanning")
            for exposure in ("exposed_ridge", "mixed", "sheltered"):
                candidates = rules.suggest_alternatives(
                    dataset, day["start_node"], day["end_node"],
                    {"max_km": caps["max_km"], "max_ascent_m": caps["max_ascent_m"],
                     "max_exposure": exposure}, 3,
                )
                scored = [
                    (c, rules.assess_risk(dataset, c["segments"], weather.risk_input()))
                    for c in candidates
                ]
                safe = [(c, r) for c, r in scored if r["band"] != "no_go"]
                if safe:
                    candidate, candidate_risk = safe[0]
                    relaxed = "none" if exposure == "exposed_ridge" else f"exposure->{exposure}"
                    print(f"    chose {candidate['segments']} "
                          f"({candidate['total_km']} km) -> "
                          f"{candidate_risk['risk_score']} {candidate_risk['band']} "
                          f"[relaxation: {relaxed}]")
                    chosen = {**day, "segments": candidate["segments"],
                              "total_km": candidate["total_km"],
                              "total_ascent_m": candidate["total_ascent_m"],
                              "ends_at_shelter": candidate["ends_at_shelter"]}
                    break
            else:
                print("    no feasible alternative - recommend shifting the dates")
        final_plan.append(chosen)

    # 4. Logistics ------------------------------------------------------------
    logistics = rules.estimate_logistics(dataset, final_plan, request["party"])
    print(f"\n[4] estimate_logistics  food_days={logistics['food_days']} "
          f"party_size={logistics['party_size']}")
    for sheet in logistics["days"]:
        print(f"    day {sheet['day']} {sheet['date']}: "
              f"{sheet['hiking_hours']} h hiking, "
              f"{sheet['daylight_margin_hours']} h daylight margin, "
              f"{sheet['water_sources']} water source(s)")
    for warning in logistics["water_warnings"]:
        print(f"    WARNING {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
