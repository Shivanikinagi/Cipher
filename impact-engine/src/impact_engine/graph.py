from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .models import EdgeKind


@dataclass
class Edge:
    src: str
    dst: str
    kind: EdgeKind
    weight: float = 1.0
    traffic_volume: float = 0.0
    call_frequency: float = 0.0
    error_rate: float = 0.0
    route: str = ""
    method: str = ""

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.src, self.dst, self.kind.value, self.route)


@dataclass
class ServiceNode:
    service: str
    endpoints: set[tuple[str, str]] = field(default_factory=set)


class HybridGraph:
    """Merged dependency graph: static import edges + runtime call edges.

    Static edges represent compile-time dependencies (weight 1.0).
    Runtime edges carry production weights (call frequency, traffic volume,
    error rate) learned from telemetry. Message-queue edges are implicit
    runtime contracts (async).
    """

    def __init__(self) -> None:
        self._nodes: dict[str, ServiceNode] = {}
        self._edges: dict[tuple[str, str, str, str], Edge] = {}

    # ----- node management -----
    def add_service(self, service: str) -> None:
        self._nodes.setdefault(service, ServiceNode(service=service))

    def add_endpoint(self, service: str, method: str, route: str) -> None:
        node = self._nodes.setdefault(service, ServiceNode(service=service))
        node.endpoints.add((method, route))

    def add_edge(
        self,
        src: str,
        dst: str,
        kind: EdgeKind,
        weight: float = 1.0,
        traffic_volume: float = 0.0,
        call_frequency: float = 0.0,
        error_rate: float = 0.0,
        route: str = "",
        method: str = "",
    ) -> None:
        self.add_service(src)
        self.add_service(dst)
        # edges are keyed per (src, dst, kind, route): two services may have
        # multiple distinct runtime contracts (GET /orders vs POST /checkout)
        key = (src, dst, kind.value, route)
        existing = self._edges.get(key)
        if existing is None:
            self._edges[key] = Edge(
                src=src,
                dst=dst,
                kind=kind,
                weight=weight,
                traffic_volume=traffic_volume,
                call_frequency=call_frequency,
                error_rate=error_rate,
                route=route,
                method=method,
            )
            return
        # merge: accumulate frequency/volume, keep max error rate, bump weight
        existing.call_frequency += call_frequency
        existing.traffic_volume += traffic_volume
        existing.error_rate = max(existing.error_rate, error_rate)
        if weight > existing.weight:
            existing.weight = weight

    # ----- querying -----
    def services(self) -> list[str]:
        return sorted(self._nodes)

    def edges(self) -> list[Edge]:
        return list(self._edges.values())

    def edge_weight(
        self, src: str, dst: str, kind: EdgeKind, route: str = ""
    ) -> float:
        e = self._edges.get((src, dst, kind.value, route))
        return e.weight if e else 0.0

    def out_edges(self, src: str) -> list[Edge]:
        return [e for e in self._edges.values() if e.src == src]

    def endpoint_owner(self, method: str, route: str) -> str | None:
        for svc, node in self._nodes.items():
            if (method, route) in node.endpoints:
                return svc
        return None

    def to_dict(self) -> dict:
        return {
            "services": sorted(self._nodes),
            "edges": [
                {
                    "src": e.src,
                    "dst": e.dst,
                    "kind": e.kind.value,
                    "weight": round(e.weight, 4),
                    "traffic_volume": round(e.traffic_volume, 2),
                    "call_frequency": round(e.call_frequency, 2),
                    "error_rate": round(e.error_rate, 4),
                    "route": e.route,
                    "method": e.method,
                }
                for e in self._edges.values()
            ],
        }

    def merge(self, other: HybridGraph) -> None:
        for svc in other.services():
            self.add_service(svc)
        for svc, node in other._nodes.items():
            for method, route in node.endpoints:
                self.add_endpoint(svc, method, route)
        for e in other.edges():
            self.add_edge(
                e.src,
                e.dst,
                e.kind,
                weight=e.weight,
                traffic_volume=e.traffic_volume,
                call_frequency=e.call_frequency,
                error_rate=e.error_rate,
                route=e.route,
                method=e.method,
            )

    # ----- grouping helpers -----
    def runtime_edges(self) -> list[Edge]:
        return [e for e in self._edges.values() if e.kind != EdgeKind.STATIC_IMPORT]

    def static_edges(self) -> list[Edge]:
        return [
            e
            for e in self._edges.values()
            if e.kind == EdgeKind.STATIC_IMPORT or e.kind == EdgeKind.MESSAGE_QUEUE
        ]

    def edge_counts(self) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for e in self._edges.values():
            counts[e.kind.value] += 1
        return dict(counts)