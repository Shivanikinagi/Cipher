from impact_engine.graph import HybridGraph
from impact_engine.models import EdgeKind

import pytest


def test_add_and_merge_edges():
    g = HybridGraph()
    g.add_edge("a", "b", EdgeKind.STATIC_IMPORT)
    assert g.edge_weight("a", "b", EdgeKind.STATIC_IMPORT) == 1.0
    g.add_edge("a", "b", EdgeKind.RUNTIME_CALL, call_frequency=50, traffic_volume=0.5)
    assert g.edge_weight("a", "b", EdgeKind.RUNTIME_CALL) == 1.0
    runtime = [e for e in g.out_edges("a") if e.kind == EdgeKind.RUNTIME_CALL][0]
    assert runtime.call_frequency == 50
    assert runtime.traffic_volume == 0.5


def test_merge_accumulates_frequency():
    g = HybridGraph()
    g.add_edge("a", "b", EdgeKind.RUNTIME_CALL, call_frequency=10, traffic_volume=0.1)
    g.add_edge("a", "b", EdgeKind.RUNTIME_CALL, call_frequency=10, traffic_volume=0.2)
    e = g.out_edges("a")[0]
    assert e.call_frequency == 20
    assert e.traffic_volume == pytest.approx(0.3)


def test_endpoints():
    g = HybridGraph()
    g.add_service("s")
    g.add_endpoint("s", "GET", "/orders")
    assert g.endpoint_owner("GET", "/orders") == "s"
    assert g.endpoint_owner("POST", "/orders") is None


def test_merge_graphs():
    a, b = HybridGraph(), HybridGraph()
    a.add_edge("x", "y", EdgeKind.STATIC_IMPORT)
    b.add_edge("y", "z", EdgeKind.RUNTIME_CALL, call_frequency=7)
    a.merge(b)
    assert set(a.services()) == {"x", "y", "z"}
    assert a.out_edges("y")[0].call_frequency == 7