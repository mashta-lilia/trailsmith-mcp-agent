"""Regenerates data/trails.geojson and data/shelters.csv from the curated tables below.

Node/segment values were curated from OSM (Geofabrik extracts for Ivano-Frankivsk
and Zakarpattia) and public Chornohora route descriptions; see data/PROVENANCE.md.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data"

# (node_id, name, lat, lon, altitude_m, nearest_settlement)
NODES = [
    # nearest_settlement values are limited to towns that OpenWeatherMap can
    # resolve (Vorokhta, Yasinia, Rakhiv, Verkhovyna); tiny hamlets are mapped
    # to their district town.
    ("ZAROSLYAK", "Zaroslyak base", 48.1636, 24.5364, 1220, "Vorokhta,UA"),
    ("HOVERLA", "Hoverla summit", 48.1601, 24.5003, 2061, "Yasinia,UA"),
    ("KOZMESHCHYK", "Kozmeshchyk clearing", 48.1846, 24.4726, 1240, "Yasinia,UA"),
    ("LAZESHCHYNA", "Lazeshchyna village", 48.2364, 24.5169, 682, "Yasinia,UA"),
    ("PETROS", "Petros summit", 48.1728, 24.4139, 2020, "Rakhiv,UA"),
    ("KVASY", "Kvasy village", 48.1461, 24.2811, 600, "Rakhiv,UA"),
    ("BRESKUL_SADDLE", "Breskul saddle", 48.1494, 24.5106, 1850, "Vorokhta,UA"),
    ("POZHYZHEVSKA", "Pozhyzhevska meteo station", 48.1466, 24.5306, 1822, "Vorokhta,UA"),
    ("NESAMOVYTE", "Lake Nesamovyte", 48.1225, 24.5342, 1750, "Vorokhta,UA"),
    ("TURKUL", "Turkul summit", 48.1183, 24.5250, 2036, "Vorokhta,UA"),
    ("SHPYTSI", "Shpytsi rocks", 48.1094, 24.5697, 1863, "Verkhovyna,UA"),
    ("SMOTRYCH", "Smotrych summit", 48.0653, 24.6117, 1894, "Verkhovyna,UA"),
    ("POP_IVAN", "Pip Ivan summit (observatory)", 48.0489, 24.6272, 2028, "Verkhovyna,UA"),
    ("MARICHEIKA", "Lake Maricheika", 48.0561, 24.6503, 1510, "Verkhovyna,UA"),
    ("SHYBENE", "Shybene hamlet", 48.0208, 24.7092, 780, "Verkhovyna,UA"),
    ("FOREST_PRUT", "Prut valley trail junction", 48.1400, 24.5500, 1400, "Vorokhta,UA"),
    ("BYSTRETS", "Bystrets village", 48.1069, 24.6300, 900, "Verkhovyna,UA"),
    ("DZEMBRONIA", "Dzembronia village", 48.0800, 24.6636, 850, "Verkhovyna,UA"),
]

# (segment_id, from_node, to_node, length_km, ascent_m, descent_m,
#  max_altitude_m, exposure, river_crossings, surface)
# ascent/descent are given in the from->to direction; traversal in the
# opposite direction swaps them.
SEGMENTS = [
    ("CH-001", "ZAROSLYAK", "HOVERLA", 4.2, 850, 10, 2061, "exposed_ridge", 0, "rocky"),
    ("CH-002", "HOVERLA", "BRESKUL_SADDLE", 2.5, 120, 330, 2061, "exposed_ridge", 0, "rocky"),
    ("CH-003", "BRESKUL_SADDLE", "POZHYZHEVSKA", 1.8, 90, 120, 1911, "exposed_ridge", 0, "grass"),
    ("CH-004", "POZHYZHEVSKA", "NESAMOVYTE", 3.0, 150, 220, 1880, "mixed", 0, "grass"),
    ("CH-005", "ZAROSLYAK", "POZHYZHEVSKA", 3.6, 640, 40, 1822, "mixed", 0, "forest"),
    ("CH-006", "ZAROSLYAK", "FOREST_PRUT", 2.8, 200, 20, 1400, "sheltered", 1, "forest"),
    ("CH-007", "FOREST_PRUT", "NESAMOVYTE", 3.4, 380, 30, 1750, "sheltered", 1, "forest"),
    ("CH-008", "NESAMOVYTE", "TURKUL", 1.6, 290, 0, 2036, "exposed_ridge", 0, "rocky"),
    ("CH-009", "TURKUL", "SHPYTSI", 3.2, 180, 350, 2036, "exposed_ridge", 0, "rocky"),
    ("CH-010", "SHPYTSI", "SMOTRYCH", 4.8, 320, 290, 1894, "exposed_ridge", 0, "grass"),
    ("CH-011", "SMOTRYCH", "POP_IVAN", 2.6, 240, 100, 2028, "exposed_ridge", 0, "rocky"),
    ("CH-012", "POP_IVAN", "MARICHEIKA", 2.9, 20, 540, 2028, "mixed", 0, "grass"),
    ("CH-013", "MARICHEIKA", "SHYBENE", 6.5, 60, 790, 1510, "sheltered", 2, "forest"),
    ("CH-014", "SHPYTSI", "BYSTRETS", 5.4, 30, 990, 1863, "sheltered", 1, "forest"),
    ("CH-015", "BYSTRETS", "DZEMBRONIA", 4.6, 120, 170, 950, "sheltered", 1, "dirt road"),
    ("CH-016", "DZEMBRONIA", "SMOTRYCH", 5.8, 1060, 20, 1894, "mixed", 0, "forest"),
    ("CH-017", "HOVERLA", "KOZMESHCHYK", 4.9, 40, 860, 2061, "mixed", 0, "forest"),
    ("CH-018", "KOZMESHCHYK", "LAZESHCHYNA", 6.8, 30, 590, 1240, "sheltered", 2, "dirt road"),
    ("CH-019", "KOZMESHCHYK", "PETROS", 5.6, 800, 20, 2020, "exposed_ridge", 0, "grass"),
    ("CH-020", "PETROS", "KVASY", 7.9, 60, 1470, 2020, "mixed", 1, "forest"),
    ("CH-021", "HOVERLA", "PETROS", 6.2, 620, 660, 2061, "exposed_ridge", 0, "grass"),
    ("CH-022", "NESAMOVYTE", "SHPYTSI", 3.9, 200, 240, 1900, "mixed", 0, "grass"),
    ("CH-023", "DZEMBRONIA", "POP_IVAN", 7.2, 1190, 30, 2028, "mixed", 1, "forest"),
    ("CH-024", "FOREST_PRUT", "POZHYZHEVSKA", 2.2, 430, 10, 1822, "sheltered", 0, "forest"),
]

# (node_id, shelter_name, type, capacity)
SHELTERS = [
    ("ZAROSLYAK", "Zaroslyak sport base", "hut", 60),
    ("KOZMESHCHYK", "Kozmeshchyk camp", "camp", 30),
    ("NESAMOVYTE", "Nesamovyte lake camp", "camp", 25),
    ("MARICHEIKA", "Maricheika lake camp", "camp", 25),
    ("LAZESHCHYNA", "Lazeshchyna guesthouses", "guesthouse", 40),
    ("KVASY", "Kvasy guesthouses", "guesthouse", 40),
    ("BYSTRETS", "Bystrets meadow camp", "camp", 20),
    ("DZEMBRONIA", "Dzembronia guesthouses", "guesthouse", 30),
    ("SHYBENE", "Shybene camp", "camp", 15),
]


def build() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    node_lookup = {n[0]: n for n in NODES}
    features = []
    for node_id, name, lat, lon, alt, settlement in NODES:
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "node_id": node_id,
                "name": name,
                "altitude_m": alt,
                "nearest_settlement": settlement,
            },
        })
    for (seg_id, frm, to, km, asc, desc, max_alt, exposure, rivers, surface) in SEGMENTS:
        a, b = node_lookup[frm], node_lookup[to]
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [[a[3], a[2]], [b[3], b[2]]],
            },
            "properties": {
                "segment_id": seg_id,
                "from_node": frm,
                "to_node": to,
                "length_km": km,
                "ascent_m": asc,
                "descent_m": desc,
                "max_altitude_m": max_alt,
                "exposure": exposure,
                "river_crossings": rivers,
                "surface": surface,
            },
        })
    geojson = {"type": "FeatureCollection", "features": features}
    (DATA_DIR / "trails.geojson").write_text(
        json.dumps(geojson, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    with (DATA_DIR / "shelters.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["node_id", "shelter_name", "type", "capacity"])
        writer.writerows(SHELTERS)
    print(f"Wrote {len(NODES)} nodes, {len(SEGMENTS)} segments, {len(SHELTERS)} shelters to {DATA_DIR}")


if __name__ == "__main__":
    build()
