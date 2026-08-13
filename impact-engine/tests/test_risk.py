from impact_engine.graph import HybridGraph
from impact_engine.models import EdgeKind
from impact_engine.risk import (
    blast_radius,
    classify,
    detect_broken_endpoints,
    impact_radius,
    reverse_radius,
    risk_score,
)


def _chain_graph() -> HybridGraph:
    g = HybridGraph()
    g.add_edge("a", "b", EdgeKind.STATIC_IMPORT, weight=1.0)
    g.add_edge("b", "c", EdgeKind.RUNTIME_CALL, weight=0.5, route="/c", method="GET")
    g.add_edge("c", "d", EdgeKind.RUNTIME_CALL, weight=0.5, route="/d", method="POST")
    return g


def test_blast_radius_decay_math():
    g = _chain_graph()
    blast = blast_radius(g, ["a"], decay=0.5)
    scores = {b.service: b.score for b in blast}
    assert scores["a"] == 1.0
    assert scores["b"] == 0.5  # 1.0 * 1.0 * 0.5
    assert scores["c"] == 0.125  # 1.0 * 1.0 * 0.5 * 0.5 * 0.5
    assert abs(scores["d"] - 0.03125) < 1e-4


def test_reverse_radius_finds_callers():
    g = _chain_graph()
    reverse = reverse_radius(g, ["d"])
    svcs = {b.service for b in reverse}
    assert {"c", "b", "a", "d"} <= svcs


def test_impact_radius_union():
    g = _chain_graph()
    forward = {b.service for b in blast_radius(g, ["b"])}
    impact = {b.service for b in impact_radius(g, ["b"])}
    assert "a" in impact  # caller
    assert "c" in impact and "d" in impact  # dependents
    assert impact >= forward


def test_detect_broken_endpoints():
    g = HybridGraph()
    g.add_edge("gw", "orders", EdgeKind.RUNTIME_CALL, route="/orders", method="GET", traffic_volume=0.9)
    g.add_edge("gw", "users", EdgeKind.RUNTIME_CALL, route="/me", method="GET", traffic_volume=0.7)
    g.add_endpoint("orders", "GET", "/orders")
    broken = detect_broken_endpoints(g, {("orders", "GET", "/orders")})
    assert len(broken) == 1
    assert broken[0]["caller"] == "gw"
    assert broken[0]["callee"] == "orders"


def test_risk_score_and_classify():
    assert classify(risk_score(1.0, 1.0, 1.0, 1.0)) == "High"
    assert classify(risk_score(0.6, 0.5, 0.2, 0.1)) in ("High", "Medium")
    assert classify(risk_score(0.1, 0.0, 0.0, 0.0)) == "Low"
    assert 0.0 <= risk_score(1.0, 1.0, 1.0, 1.0) <= 1.0