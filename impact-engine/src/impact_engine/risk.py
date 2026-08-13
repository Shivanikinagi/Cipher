"""Blast-radius traversal and risk scoring over the hybrid graph."""
from __future__ import annotations

import heapq
from dataclasses import dataclass

from .graph import HybridGraph
from .incidents import IncidentStore
from .models import EdgeKind, RiskLevel

MAX_DEPTH = 8
DECAY = 0.5
BLAST_CAP = 1.0
RISK_WEIGHTS = {
    "blast": 0.45,
    "traffic": 0.25,
    "error": 0.15,
    "incident": 0.15,
}


@dataclass
class BlastResult:
    service: str
    score: float
    distance: int
    reasons: list[str]

    def __post_init__(self) -> None:
        self.score = max(0.0, min(BLAST_CAP, self.score))


def blast_radius(
    graph: HybridGraph,
    seed_services: list[str],
    decay: float = DECAY,
    max_depth: int = MAX_DEPTH,
) -> list[BlastResult]:
    """Weighted multi-source traversal (forward: dependents).

    score(dst) = max over paths of edge_weight(path) * decay ** distance.
    We deliberately keep the runtime call frequency/volume folded into edge
    weight (see telemetry.to_edges) so hot paths propagate further.
    """
    results = _weighted_traversal(
        graph, seed_services, reverse=False, decay=decay, max_depth=max_depth
    )
    results.sort(key=lambda r: r.score, reverse=True)
    return results


def reverse_radius(
    graph: HybridGraph,
    seed_services: list[str],
    decay: float = DECAY,
    max_depth: int = MAX_DEPTH,
) -> list[BlastResult]:
    """Weighted traversal along incoming edges (callers/dependents)."""
    results = _weighted_traversal(
        graph, seed_services, reverse=True, decay=decay, max_depth=max_depth
    )
    results.sort(key=lambda r: r.score, reverse=True)
    return results


def impact_radius(
    graph: HybridGraph,
    seed_services: list[str],
    decay: float = DECAY,
    max_depth: int = MAX_DEPTH,
) -> list[BlastResult]:
    """Full impact set: callers (reverse) + dependencies (forward).

    Changing a service can break both the services that call it (contract
    change) and the services it calls (payload/usage change), so the impact
    radius is the union of both traversals.
    """
    merged: dict[str, BlastResult] = {}
    for result in blast_radius(graph, seed_services, decay, max_depth) + reverse_radius(
        graph, seed_services, decay, max_depth
    ):
        prev = merged.get(result.service)
        if prev is None or result.score > prev.score:
            merged[result.service] = result
    merged_result = list(merged.values())
    merged_result.sort(key=lambda r: r.score, reverse=True)
    return merged_result


def _weighted_traversal(
    graph: HybridGraph,
    seed_services: list[str],
    reverse: bool,
    decay: float,
    max_depth: int,
) -> list[BlastResult]:
    best: dict[str, tuple[float, int]] = {}
    heap: list[tuple[float, int, str]] = []
    for seed in set(seed_services):
        if seed not in graph.services():
            continue
        best[seed] = (BLAST_CAP, 0)
        heapq.heappush(heap, (-BLAST_CAP, 0, seed))

    while heap:
        neg_score, dist, svc = heapq.heappop(heap)
        score = -neg_score
        cur_score, cur_dist = best[svc]
        if cur_score > score or cur_dist < dist:
            continue
        if dist >= max_depth:
            continue
        edges = graph.out_edges(svc) if not reverse else _in_edges(graph, svc)
        for edge in edges:
            ndist = dist + 1
            nscore = score * edge.weight * decay
            prev = best.get(edge.dst if not reverse else edge.src)
            target = edge.dst if not reverse else edge.src
            if prev is None or nscore > prev[0]:
                best[target] = (nscore, ndist)
                heapq.heappush(heap, (-nscore, ndist, target))

    return [
        BlastResult(
            service=svc,
            score=round(s, 4),
            distance=d,
            reasons=[],
        )
        for svc, (s, d) in best.items()
        if s > 0.01
    ]


def _in_edges(graph: HybridGraph, svc: str) -> list:
    return [e for e in graph.edges() if e.dst == svc]


def detect_broken_endpoints(
    graph: HybridGraph, changed_routes: set[tuple[str, str, str]]
) -> list[dict]:
    """Find callers of changed endpoints via runtime call edges.

    changed_routes: {(service, method, route)}. For every runtime edge whose
    dst service owns a changed route matching the edge route, the calling
    (src) endpoint is at risk of breaking.
    """
    broken: list[dict] = []
    for edge in graph.runtime_edges():
        if edge.route and edge.route.startswith("/"):
            changed = (edge.dst, edge.method, edge.route) in changed_routes
            if changed:
                broken.append(
                    {
                        "caller": edge.src,
                        "caller_method": edge.method,
                        "caller_route": edge.route,
                        "callee": edge.dst,
                        "traffic_volume": edge.traffic_volume,
                        "error_rate": edge.error_rate,
                    }
                )
    return broken


def risk_score(
    blast: float,
    traffic_volume: float,
    error_rate: float,
    incident_factor: float,
) -> float:
    score = (
        RISK_WEIGHTS["blast"] * blast
        + RISK_WEIGHTS["traffic"] * min(1.0, traffic_volume)
        + RISK_WEIGHTS["error"] * min(1.0, error_rate)
        + RISK_WEIGHTS["incident"] * incident_factor
    )
    return round(max(0.0, min(1.0, score)), 4)


def classify(score: float) -> RiskLevel:
    if score >= 0.7:
        return RiskLevel.HIGH
    if score >= 0.4:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def aggregate_per_service(
    graph: HybridGraph,
    blasts: list[BlastResult],
    broken: list[dict],
    incident_factor: float,
) -> list[dict]:
    """Merge blast + traffic/error signals per affected service."""
    traffic: dict[str, float] = {}
    error: dict[str, float] = {}
    callers: dict[str, list[str]] = {}
    for edge in graph.edges():
        traffic[edge.dst] = max(traffic.get(edge.dst, 0.0), edge.traffic_volume)
        error[edge.dst] = max(error.get(edge.dst, 0.0), edge.error_rate)
    for b in broken:
        callers.setdefault(b["callee"], []).append(
            f"{b['caller']} calls {b['caller_method']} {b['caller_route']}"
        )

    out = []
    for b in blasts:
        reasons = list(b.reasons)
        if traffic.get(b.service, 0.0) > 0.15:
            reasons.append(
                f"carries {traffic[b.service] * 100:.0f}% of observed traffic"
            )
        if error.get(b.service, 0.0) > 0.05:
            reasons.append(f"{error[b.service] * 100:.0f}% error rate on hot paths")
        if b.service in callers:
            reasons.extend(callers[b.service])
        score = risk_score(
            blast=b.score,
            traffic_volume=traffic.get(b.service, 0.0),
            error_rate=error.get(b.service, 0.0),
            incident_factor=incident_factor,
        )
        out.append(
            {
                "service": b.service,
                "risk_level": classify(score).value,
                "risk_score": score,
                "blast_score": b.score,
                "reasons": reasons,
            }
        )
    return out


def edge_kind_summary(graph: HybridGraph) -> dict:
    return graph.edge_counts()