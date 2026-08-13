"""OpenTelemetry ingestion: traces and metrics -> runtime dependency edges.

Accepts OTLP/HTTP JSON (simplified envelope) and flat batches. Server spans
are paired with their parent (client) span to reconstruct a distributed call
graph. Weights are derived from production signals:

- call_frequency: calls per minute from span counts (bucketed by hour)
- traffic_volume: normalized request rate across edges
- error_rate:   fraction of errored spans per edge
- weight:       min(1, call_frequency * (1 + error_rate * 10)) with
                traffic normalization applied at blast-radius time
"""
from __future__ import annotations

import math
from collections import defaultdict

from .graph import HybridGraph
from .models import EdgeKind, MetricPoint, TraceSpan


class RuntimeTelemetry:
    """Ingest store that folds traces/metrics into runtime edges."""

    def __init__(self) -> None:
        self._spans: list[TraceSpan] = []
        self._metrics: list[MetricPoint] = []
        self._hours: dict[str, set[str]] = defaultdict(set)

    # ------------------------------------------------------------------
    # ingestion
    # ------------------------------------------------------------------
    def ingest_spans(self, spans: list[TraceSpan | dict]) -> int:
        """Pair client/server spans across services and register edges."""
        spans = [s if isinstance(s, TraceSpan) else TraceSpan(**s) for s in spans]
        before = len(self._spans)
        for span in spans:
            self._spans.append(span)
            self._hours[span.trace_id].add(span.span_id)
        by_id: dict[str, TraceSpan] = {
            s.span_id: s for s in spans if s.service_name
        }
        added = 0
        for span in spans:
            if span.kind not in ("SPAN_KIND_SERVER", "SPAN_KIND_CONSUMER"):
                continue  # client spans are parents, never edge targets
            parent = by_id.get(span.parent_span_id or "")
            if parent is None or parent.service_name == span.service_name:
                continue
            self._record_runtime_edge(parent, span)
            added += 1
        return added

    def ingest_metrics(self, metrics: list[MetricPoint]) -> None:
        self._metrics.extend(metrics)

    # ------------------------------------------------------------------
    # edge materialization
    # ------------------------------------------------------------------
    def to_edges(self, window_minutes: int = 60) -> list[tuple]:
        """Materialize runtime edges from ingested telemetry.

        Returns tuples suitable for ``HybridGraph.add_edge``.
        """
        counters: dict[tuple[str, str], dict] = defaultdict(
            lambda: {
                "calls": 0,
                "errors": 0,
                "latency_ms": 0.0,
                "routes": set(),
                "methods": set(),
            }
        )
        for span in self._spans:
            # edge attribution was recorded at ingest time on child server
            # spans (edge_src/edge_dst attributes); client spans carry none
            src = span.attributes.get("edge_src")
            dst = span.attributes.get("edge_dst")
            if not src or not dst:
                continue
            agg = counters[(src, dst)]
            agg["calls"] += 1
            if span.is_error:
                agg["errors"] += 1
            agg["latency_ms"] += span.duration_ms
            route = span.attributes.get("http.route")
            method = span.attributes.get("http.method")
            if route:
                agg["routes"].add(str(route))
            if method:
                agg["methods"].add(str(method))

        traffic = {k: v["calls"] for k, v in counters.items()}
        max_traffic = max(traffic.values()) if traffic else 1.0

        edges: list[tuple] = []
        for (src, dst), agg in counters.items():
            freq = agg["calls"] * (60.0 / max(window_minutes, 1))
            error_rate = agg["errors"] / max(agg["calls"], 1)
            volume = agg["calls"] / max_traffic
            raw_weight = freq * (1.0 + error_rate * 10.0)
            weight = min(1.0, raw_weight / 50.0)
            route = next(iter(agg["routes"]), "")
            method = next(iter(agg["methods"]), "GET")
            edges.append(
                {
                    "src": src,
                    "dst": dst,
                    "kind": EdgeKind.RUNTIME_CALL,
                    "weight": weight,
                    "traffic_volume": volume,
                    "call_frequency": freq,
                    "error_rate": error_rate,
                    "route": route,
                    "method": method,
                }
            )
        return edges

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _record_runtime_edge(
        self, parent: TraceSpan, child: TraceSpan
    ) -> None:
        # annotate child server span with the edge endpoints so
        # materialization can attribute traffic correctly
        child.attributes.setdefault("edge_src", parent.service_name)
        child.attributes.setdefault("edge_dst", child.service_name)

    def span_count(self) -> int:
        return len(self._spans)

    def edge_stats(self) -> dict:
        edges = self.to_edges()
        return {
            "runtime_edges": len(edges),
            "spans": len(self._spans),
            "metrics": len(self._metrics),
            "total_error_rate": (
                sum(e["error_rate"] * e["call_frequency"] for e in edges)
                / max(sum(e["call_frequency"] for e in edges), 1.0)
            ),
        }


def apply_edges_to_graph(graph: HybridGraph, edges: list[dict]) -> int:
    added = 0
    for e in edges:
        graph.add_edge(
            src=e["src"],
            dst=e["dst"],
            kind=e["kind"],
            weight=e["weight"],
            traffic_volume=e["traffic_volume"],
            call_frequency=e["call_frequency"],
            error_rate=e["error_rate"],
            route=e["route"],
            method=e["method"],
        )
        added += 1
    return added


def weighted_normalize(log_scale: bool = False) -> None:
    """Placeholder hook for future normalization tuning (kept explicit)."""
    _ = log_scale
    return None