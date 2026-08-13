from impact_engine.graph import HybridGraph
from impact_engine.models import EdgeKind, TraceSpan
from impact_engine.telemetry import apply_edges_to_graph, RuntimeTelemetry


def _span(
    trace: str,
    sid: str,
    parent: str | None,
    svc: str,
    kind: str,
    route: str = "/x",
    method: str = "GET",
    error: bool = False,
    dur_ms: float = 50.0,
) -> TraceSpan:
    return TraceSpan(
        trace_id=trace,
        span_id=sid,
        parent_span_id=parent,
        service_name=svc,
        name=f"HTTP {method} {route}",
        kind=kind,
        status="STATUS_CODE_ERROR" if error else "STATUS_CODE_OK",
        attributes={"http.route": route, "http.method": method},
        start_time_unix_nano=1_000_000_000,
        end_time_unix_nano=1_000_000_000 + int(dur_ms * 1e6),
    )


def test_client_server_pairing_creates_runtime_edge():
    tel = RuntimeTelemetry()
    spans = [
        _span("t1", "gw-1", None, "api-gateway", "SPAN_KIND_SERVER", route="/orders"),
        _span("t1", "gw-c1", "gw-1", "api-gateway", "SPAN_KIND_CLIENT"),
        _span("t1", "ord-1", "gw-c1", "order-service", "SPAN_KIND_SERVER", route="/orders"),
    ]
    added = tel.ingest_spans(spans)
    assert added == 1
    edges = tel.to_edges()
    assert len(edges) == 1
    e = edges[0]
    assert e["src"] == "api-gateway"
    assert e["dst"] == "order-service"
    assert e["route"] == "/orders"
    assert e["kind"] == EdgeKind.RUNTIME_CALL


def test_error_rate_and_weight_reflect_errors():
    tel = RuntimeTelemetry()
    spans = []
    for i in range(10):
        spans.append(_span("t1", f"gw-{i}", None, "gw", "SPAN_KIND_SERVER"))
        spans.append(_span("t1", f"gw-c{i}", f"gw-{i}", "gw", "SPAN_KIND_CLIENT"))
        spans.append(
            _span(
                "t1",
                f"svc-{i}",
                f"gw-c{i}",
                "svc",
                "SPAN_KIND_SERVER",
                error=i < 3,
            )
        )
    tel.ingest_spans(spans)
    edges = tel.to_edges()
    assert len(edges) == 1
    assert edges[0]["error_rate"] == 0.3
    assert edges[0]["call_frequency"] == 10.0  # 10 calls observed, 60-min window


def test_apply_edges_to_graph():
    graph = HybridGraph()
    edges = [
        {
            "src": "a",
            "dst": "b",
            "kind": EdgeKind.RUNTIME_CALL,
            "weight": 0.42,
            "traffic_volume": 0.8,
            "call_frequency": 100.0,
            "error_rate": 0.05,
            "route": "/x",
            "method": "GET",
        }
    ]
    assert apply_edges_to_graph(graph, edges) == 1
    assert graph.edge_weight("a", "b", EdgeKind.RUNTIME_CALL, route="/x") == 0.42