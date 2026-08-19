"""Domain rules: fitness caps, itinerary validation, risk heuristics, logistics."""
from __future__ import annotations

from typing import Any

import networkx as nx

from .dataset import EXPOSURE_ORDER, TrailDataset

# Per-day caps by party fitness.
FITNESS_CAPS: dict[str, dict[str, float]] = {
    "low": {"max_km": 12.0, "max_ascent_m": 700},
    "moderate": {"max_km": 18.0, "max_ascent_m": 1100},
    "high": {"max_km": 24.0, "max_ascent_m": 1600},
}

RISK_BANDS = [(70, "no_go"), (35, "caution"), (0, "ok")]

# Simple paths enumerated per target before giving up (see suggest_alternatives).
MAX_PATHS_SCANNED = 20


def band_for(score: int) -> str:
    for threshold, band in RISK_BANDS:
        if score >= threshold:
            return band
    return "ok"


def walk_chain(dataset: TrailDataset, segment_ids: list[str], start: str) -> dict[str, Any]:
    """Walk an oriented chain from `start`, accumulating distance/ascent."""
    current = start
    total_km = 0.0
    total_ascent = 0
    max_alt = 0
    for seg_id in segment_ids:
        seg = dataset.segments[seg_id]
        total_km += seg.length_km
        total_ascent += seg.ascent_from(current)
        max_alt = max(max_alt, seg.max_altitude_m)
        current = seg.other_end(current)
    return {
        "end": current,
        "total_km": round(total_km, 1),
        "total_ascent_m": total_ascent,
        "max_altitude_m": max_alt,
    }


def validate_itinerary(dataset: TrailDataset, itinerary: dict, party: dict) -> dict[str, Any]:
    violations: list[dict[str, Any]] = []
    caps = FITNESS_CAPS[party["fitness"]]
    normalized_days: list[dict[str, Any]] = []
    prev_end: str | None = None

    dates = [d["date"] for d in itinerary["days"]]
    for i in range(1, len(dates)):
        if dates[i] <= dates[i - 1]:
            violations.append({
                "code": "DATE_SEQUENCE_INVALID", "severity": "hard", "day": i + 1,
                "message": f"Day {i + 1} date {dates[i]} is not after {dates[i - 1]}.",
            })

    for index, day in enumerate(itinerary["days"], start=1):
        segment_ids = day["segments"]
        endpoints = dataset.chain_endpoints(segment_ids)
        if endpoints is None:
            violations.append({
                "code": "DISCONNECTED_DAY",
                "severity": "hard",
                "day": index,
                "message": f"Day {index} segments do not form a connected chain.",
            })
            normalized_days.append({
                "date": day["date"], "segments": segment_ids,
                "start_node": None, "end_node": None,
                "start_settlement": None, "end_settlement": None,
                "total_km": 0.0, "total_ascent_m": 0, "max_altitude_m": 0,
                "ends_at_shelter": False,
            })
            prev_end = None
            continue

        start, end = endpoints
        # Orient the chain to continue from the previous day when possible.
        # The segment list must be reversed too: walk_chain consumes it in
        # order, so swapping only the endpoint labels would cost the day in the
        # wrong direction and produce an impossible start == end.
        if prev_end is not None and start != prev_end and end == prev_end:
            segment_ids = list(reversed(segment_ids))
            start = prev_end
        if prev_end is not None and start != prev_end:
            violations.append({
                "code": "DAY_BOUNDARY_GAP",
                "severity": "hard",
                "day": index,
                "message": (
                    f"Day {index} starts at {start} but day {index - 1} ended at {prev_end}."
                ),
            })

        stats = walk_chain(dataset, segment_ids, start)
        end = stats["end"]
        if stats["total_km"] > caps["max_km"]:
            violations.append({
                "code": "DAILY_DISTANCE_EXCEEDED",
                "severity": "soft",
                "day": index,
                "message": (
                    f"Day {index} covers {stats['total_km']} km; cap for "
                    f"{party['fitness']} fitness is {caps['max_km']} km."
                ),
            })
        if stats["total_ascent_m"] > caps["max_ascent_m"]:
            violations.append({
                "code": "DAILY_ASCENT_EXCEEDED",
                "severity": "soft",
                "day": index,
                "message": (
                    f"Day {index} climbs {stats['total_ascent_m']} m; cap for "
                    f"{party['fitness']} fitness is {caps['max_ascent_m']} m."
                ),
            })
        is_last_day = index == len(itinerary["days"])
        if not is_last_day and not dataset.has_shelter(end) and not party["has_tent"]:
            violations.append({
                "code": "NO_SHELTER_AT_NIGHT",
                "severity": "hard",
                "day": index,
                "message": (
                    f"Day {index} ends at {end}, which has no shelter, and the party "
                    "has no tent."
                ),
            })

        normalized_days.append({
            "date": day["date"],
            # Reflects the orientation actually costed, which may be reversed
            # relative to the caller's input.
            "segments": segment_ids,
            "start_node": start,
            "end_node": end,
            "start_settlement": dataset.nodes[start].nearest_settlement,
            "end_settlement": dataset.nodes[end].nearest_settlement,
            **{k: v for k, v in stats.items() if k != "end"},
            "ends_at_shelter": dataset.has_shelter(end),
        })
        prev_end = end

    status = "invalid" if violations else "ok"
    return {
        "status": status,
        "normalized_itinerary": {"days": normalized_days},
        "violations": violations,
        # Published so callers (and the replanner subagent) use the same caps
        # the server applied, instead of inventing constraint values.
        "applied_caps": {
            "fitness": party["fitness"],
            "max_km": caps["max_km"],
            "max_ascent_m": caps["max_ascent_m"],
        },
    }


def assess_risk(dataset: TrailDataset, segment_ids: list[str], weather: dict,
                weather_known: bool = True) -> dict[str, Any]:
    factors: list[dict[str, Any]] = []
    segs = [dataset.segments[s] for s in segment_ids]
    worst_exposure = max(EXPOSURE_ORDER[s.exposure] for s in segs)
    max_alt = max(s.max_altitude_m for s in segs)
    crossings = sum(s.river_crossings for s in segs)

    if not weather_known:
        factors.append({
            "rule": "weather_unknown",
            "contribution": 40,
            "detail": "No reliable forecast; scoring conservatively.",
        })
        score = min(100, sum(f["contribution"] for f in factors))
        return {"risk_score": score, "band": band_for(score), "factors": factors}

    wind = weather["wind_ms"]

    # Exposure-scaled rather than all-or-nothing: a thunderstorm on an exposed
    # 2000 m ridge is the textbook no-go and must clear the band threshold on
    # its own, but a storm still matters in forest.
    if weather.get("thunderstorm"):
        contribution = (10, 30, 70)[worst_exposure]
        factors.append({
            "rule": f"thunderstorm_on_{('sheltered', 'mixed', 'exposed_ridge')[worst_exposure]}",
            "contribution": contribution,
            "detail": (
                "Thunderstorm forecast on "
                f"{('sheltered terrain', 'partly open terrain', 'an exposed ridge')[worst_exposure]}."
            ),
        })

    # Floor terms: severe conditions are dangerous at ANY exposure, so these are
    # deliberately not gated on terrain.
    if wind >= 25:
        factors.append({
            "rule": "severe_wind_any_terrain",
            "contribution": 50,
            "detail": f"Wind {wind} m/s is dangerous regardless of terrain.",
        })
    if weather["temp_min_c"] < -20:
        factors.append({
            "rule": "extreme_cold_any_terrain",
            "contribution": 50,
            "detail": f"Minimum {weather['temp_min_c']} C risks cold injury regardless of terrain.",
        })

    if worst_exposure >= 1 and wind >= 15:
        contribution = 45 if wind >= 20 else 30
        if worst_exposure == 1:
            contribution //= 2
        factors.append({
            "rule": "high_wind_on_open_terrain",
            "contribution": contribution,
            "detail": f"Wind {wind} m/s on {'exposed ridge' if worst_exposure == 2 else 'open sections'}.",
        })
    precip = weather["precip_mm"]
    if precip >= 10 and crossings:
        factors.append({
            "rule": "swollen_river_crossings",
            "contribution": min(30, 10 * crossings),
            "detail": f"{precip} mm precipitation with {crossings} river crossing(s).",
        })
    if precip >= 20:
        factors.append({
            "rule": "heavy_precipitation",
            "contribution": 20,
            "detail": f"Heavy precipitation ({precip} mm) forecast.",
        })
    # Precipitation previously only mattered via river crossings, which ignored
    # the classic Chornohora hazard: rain on a high exposed ridge means wet rock,
    # fog, and no escape from wind chill.
    if precip >= 10 and worst_exposure == 2 and max_alt > 1800:
        factors.append({
            "rule": "wet_exposed_ridge",
            "contribution": 35,
            "detail": (
                f"{precip} mm precipitation on an exposed ridge at {max_alt} m: "
                "wet rock, poor visibility, no shelter."
            ),
        })
    temp_min = weather["temp_min_c"]
    if temp_min < -5 and max_alt > 1800:
        factors.append({
            "rule": "cold_at_altitude",
            "contribution": 30 if temp_min < -15 else 15,
            "detail": f"Minimum {temp_min} C with route above 1800 m.",
        })

    score = min(100, sum(f["contribution"] for f in factors))
    return {"risk_score": score, "band": band_for(score), "factors": factors}


def suggest_alternatives(dataset: TrailDataset, start_node: str, end_node: str,
                         constraints: dict, k: int) -> list[dict[str, Any]]:
    max_exposure = EXPOSURE_ORDER[constraints["max_exposure"]]

    def allowed(u: str, v: str) -> bool:
        seg = dataset.graph.edges[u, v]["segment"]
        return EXPOSURE_ORDER[seg.exposure] <= max_exposure

    subgraph = nx.subgraph_view(dataset.graph, filter_edge=allowed)
    targets = [end_node] if end_node != "flexible" else [
        n for n in dataset.shelters if n != start_node
    ]

    candidates: list[dict[str, Any]] = []
    for target in targets:
        if target not in subgraph or start_node not in subgraph:
            continue
        try:
            # NetworkXNoPath is raised lazily during iteration, so the loop
            # itself must sit inside the try block.
            paths = nx.shortest_simple_paths(subgraph, start_node, target, weight="weight")
            accepted = 0
            for path_index, path in enumerate(paths):
                # Scan bound is separate from the accept count: ascent is NOT
                # monotone in path length, so truncating the scan at k would
                # hide a longer-but-flatter route that satisfies the caps.
                if path_index >= MAX_PATHS_SCANNED or accepted >= k:
                    break
                segment_ids = [
                    dataset.graph.edges[u, v]["segment"].segment_id
                    for u, v in zip(path, path[1:])
                ]
                if not segment_ids:
                    continue  # degenerate start == end path
                stats = walk_chain(dataset, segment_ids, path[0])
                if stats["total_km"] > constraints["max_km"]:
                    break  # paths arrive in increasing length; all later ones bust it too
                if stats["total_ascent_m"] > constraints["max_ascent_m"]:
                    continue
                accepted += 1
                ends_at_shelter = dataset.has_shelter(path[-1])
                candidates.append({
                    "segments": segment_ids,
                    "total_km": stats["total_km"],
                    "total_ascent_m": stats["total_ascent_m"],
                    "end_node": path[-1],
                    "ends_at_shelter": ends_at_shelter,
                    "detour_score": round(
                        stats["total_km"] + stats["total_ascent_m"] / 200
                        + (0 if ends_at_shelter else 5),
                        1,
                    ),
                })
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            continue

    candidates.sort(key=lambda c: c["detour_score"])
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for cand in candidates:
        key = tuple(cand["segments"])
        if key not in seen:
            seen.add(key)
            deduped.append(cand)
    return deduped[:k]


def estimate_logistics(dataset: TrailDataset, normalized_days: list[dict],
                       party: dict) -> dict[str, Any]:
    day_sheets: list[dict[str, Any]] = []
    water_warnings: list[str] = []
    for index, day in enumerate(normalized_days, start=1):
        # Naismith's rule: 4 km/h plus 1 h per 600 m of ascent.
        hiking_h = round(day["total_km"] / 4 + day["total_ascent_m"] / 600, 1)
        month = int(day["date"].split("-")[1])
        daylight_h = 16 if 5 <= month <= 8 else 12 if month in (4, 9, 10) else 9
        margin_h = round(daylight_h - hiking_h, 1)
        crossings = sum(
            dataset.segments[s].river_crossings for s in day["segments"]
        )
        if crossings == 0 and day["total_km"] > 10:
            water_warnings.append(
                f"Day {index}: {day['total_km']} km with no river crossings; "
                "carry full water supply."
            )
        day_sheets.append({
            "day": index,
            "date": day["date"],
            "hiking_hours": hiking_h,
            "daylight_hours": daylight_h,
            "daylight_margin_hours": margin_h,
            "water_sources": crossings,
            "ends_at_shelter": day["ends_at_shelter"],
        })
    return {
        "days": day_sheets,
        "food_days": len(normalized_days),
        "party_size": party["size"],
        "water_warnings": water_warnings,
    }
