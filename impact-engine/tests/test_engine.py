import pytest

from demo.ecosystem import (
    REPO_FILES,
    SERVICE_MAP,
    build_full_graph,
    build_static_only_graph,
)
from impact_engine.engine import ImpactEngine
from impact_engine.incidents import IncidentStore
from impact_engine.models import AnalysisRequest, ChangedFile


def _engine(graph, incidents=None):
    return ImpactEngine(graph, incidents or IncidentStore(), SERVICE_MAP, llm_enabled=False)


@pytest.fixture(scope="module")
def full_engine():
    return _engine(build_full_graph())


@pytest.fixture(scope="module")
def static_engine():
    return _engine(build_static_only_graph())


def _analyze(engine, path, description="change"):
    return engine.analyze(
        AnalysisRequest(
            changed_files=[ChangedFile(path=path, content=REPO_FILES.get(path, ""))],
            change_description=description,
        )
    )


def test_static_graph_is_strictly_smaller():
    full = build_full_graph()
    static = build_static_only_graph()
    assert len(full.edges()) == 16  # 7 static + 8 runtime + 1 MQ
    assert len(static.edges()) == 7  # import edges only


def test_runtime_only_service_change_hybrid_vs_static(full_engine, static_engine):
    # promo-service has NO static inbound edges: pure runtime dependency
    import asyncio

    hybrid = asyncio.run(_analyze(full_engine, "services/promo-service/app.py"))
    static = asyncio.run(_analyze(static_engine, "services/promo-service/app.py"))
    hybrid_services = {a.service for a in hybrid.affected_services}
    static_services = {a.service for a in static.affected_services}
    assert "order-service" in hybrid_services  # runtime caller found
    assert "api-gateway" in hybrid_services  # transitive caller
    assert "order-service" not in static_services  # invisible to static
    assert len(hybrid_services) > len(static_services)


def test_explanation_generated(full_engine):
    import asyncio

    result = asyncio.run(
        _analyze(full_engine, "services/order-service/app.py", "add idempotency keys to checkout")
    )
    assert len(result.explanation) > 50
    assert "order-service" in result.explanation


def test_route_change_breaks_caller_endpoint(full_engine):
    import asyncio

    changed = REPO_FILES["services/order-service/app.py"] + '\n@app.get("/orders")\n'
    result = asyncio.run(
        full_engine.analyze(
            AnalysisRequest(
                changed_files=[
                    ChangedFile(path="services/order-service/app.py", content=changed)
                ],
                change_description="change /orders route shape",
            )
        )
    )
    endpoints = {
        (b.endpoint.service, b.endpoint.method, b.endpoint.route)
        for b in result.broken_endpoints
    }
    assert ("api-gateway", "GET", "/orders") in endpoints


def test_demo_analysis_returns_result():
    import asyncio

    from demo.run import demo_analysis

    result = asyncio.run(demo_analysis())
    assert len(result.affected_services) >= 2
    assert "order-service" in {a.service for a in result.affected_services}