"""Read-only access to the trail dataset and its NetworkX graph."""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

import networkx as nx

DATA_DIR = Path(__file__).resolve().parents[1] / "data"

EXPOSURE_ORDER: dict[str, int] = {"sheltered": 0, "mixed": 1, "exposed_ridge": 2}


@dataclass(frozen=True)
class Node:
    node_id: str
    name: str
    altitude_m: int
    nearest_settlement: str


@dataclass(frozen=True)
class Segment:
    segment_id: str
    from_node: str
    to_node: str
    length_km: float
    ascent_m: int
    descent_m: int
    max_altitude_m: int
    exposure: str
    river_crossings: int
    surface: str

    def _require_endpoint(self, node_id: str) -> None:
        # Returning a plausible wrong answer here hides caller bugs, so fail loud.
        if node_id not in (self.from_node, self.to_node):
            raise ValueError(
                f"{node_id} is not an endpoint of {self.segment_id} "
                f"({self.from_node}..{self.to_node})"
            )

    def ascent_from(self, start_node: str) -> int:
        self._require_endpoint(start_node)
        return self.ascent_m if start_node == self.from_node else self.descent_m

    def other_end(self, node_id: str) -> str:
        self._require_endpoint(node_id)
        return self.to_node if node_id == self.from_node else self.from_node


class TrailDataset:
    def __init__(self, data_dir: Path = DATA_DIR) -> None:
        raw = json.loads((data_dir / "trails.geojson").read_text(encoding="utf-8"))
        self.nodes: dict[str, Node] = {}
        self.segments: dict[str, Segment] = {}
        for feature in raw["features"]:
            props = feature["properties"]
            if "node_id" in props:
                self.nodes[props["node_id"]] = Node(
                    node_id=props["node_id"],
                    name=props["name"],
                    altitude_m=props["altitude_m"],
                    nearest_settlement=props["nearest_settlement"],
                )
            else:
                self.segments[props["segment_id"]] = Segment(**props)

        self.shelters: dict[str, dict[str, str | int]] = {}
        with (data_dir / "shelters.csv").open(encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                self.shelters[row["node_id"]] = {
                    "shelter_name": row["shelter_name"],
                    "type": row["type"],
                    "capacity": int(row["capacity"]),
                }

        self.graph = nx.Graph()
        for node in self.nodes.values():
            self.graph.add_node(node.node_id)
        for seg in self.segments.values():
            self.graph.add_edge(seg.from_node, seg.to_node, segment=seg, weight=seg.length_km)

    def has_shelter(self, node_id: str) -> bool:
        return node_id in self.shelters

    def chain_endpoints(self, segment_ids: list[str]) -> tuple[str, str] | None:
        """Return (start, end) if the segments form a connected chain, else None."""
        if len(set(segment_ids)) != len(segment_ids):
            return None  # a repeated segment is not a valid point-to-point chain
        segs = [self.segments[s] for s in segment_ids]
        if len(segs) == 1:
            return segs[0].from_node, segs[0].to_node
        first, second = segs[0], segs[1]
        shared = {first.from_node, first.to_node} & {second.from_node, second.to_node}
        if len(shared) != 1:
            # 0 = not connected; 2 = parallel edges, so orientation is ambiguous.
            return None
        current = shared.pop()
        start = first.other_end(current)
        for seg in segs[1:]:
            if current not in (seg.from_node, seg.to_node):
                return None
            current = seg.other_end(current)
        return start, current


_dataset: TrailDataset | None = None


def get_dataset() -> TrailDataset:
    global _dataset
    if _dataset is None:
        _dataset = TrailDataset()
    return _dataset
