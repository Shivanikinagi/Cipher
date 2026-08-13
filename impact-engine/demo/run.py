"""In-process demo: analyze a change against the demo ecosystem graph."""
from __future__ import annotations

from impact_engine.engine import ImpactEngine
from impact_engine.incidents import IncidentStore
from impact_engine.models import AnalysisRequest, ChangedFile, Incident

from .ecosystem import SERVICE_MAP, build_full_graph


def demo_analysis():
    graph = build_full_graph()
    incidents = IncidentStore()
    incidents.add(
        Incident(
            summary="checkout p95 degraded after order-service payload change",
            affected_services=["order-service", "api-gateway"],
            tags=["latency", "contract"],
            text="deploying a payload change to order-service broke "
            "checkout for a subset of promo users; rollback restored SLOs",
        )
    )
    incidents.add(
        Incident(
            summary="promo-service flaky under load caused order failures",
            affected_services=["promo-service", "order-service"],
            tags=["flakiness", "fraud"],
            text="promo-service /validate spiked 5xx during flash sale",
        )
    )
    engine = ImpactEngine(graph, incidents, SERVICE_MAP, llm_enabled=False)
    request = AnalysisRequest(
        pr_identifier="demo#1234",
        change_description="add idempotency key to checkout flow",
        changed_files=[
            ChangedFile(
                path="services/order-service/app.py",
                content="""
import payment_client
from order_client import OrderClient

@app.get("/orders")
def list_orders():
    return OrderClient().list()

@app.post("/checkout")
def checkout(order_id=None):
    # new signature: idempotency-key header
    return {"ok": True}
""",
            )
        ],
    )
    return engine.analyze(request)