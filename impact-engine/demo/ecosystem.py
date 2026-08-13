"""Synthetic demo microservice ecosystem.

Serves as (a) a live demo repo and (b) the controlled substrate for the
benchmark. The ecosystem is deliberately asymmetric:

- ``STATIC_IMPORT`` edges are visible to any static analyzer
  (gateway -> order/search; order -> payment/inventory/warehouse; ...)
- ``RUNTIME_CALL`` edges exist only in production telemetry
  (gateway -> user/auth via dynamic discovery; order -> promo via an
  env-configured URL) — exactly the class of dependencies Depwire-style
  static tools miss
- ``MESSAGE_QUEUE`` edges are async contracts (order -> notification)

Ground truth for a change = reverse+forward reachability over the FULL
hybrid graph (what actually breaks at runtime); the static-only baseline
only sees static edges.
"""
from __future__ import annotations

from impact_engine.graph import HybridGraph
from impact_engine.models import EdgeKind

SERVICES = [
    "api-gateway",
    "auth-service",
    "user-service",
    "order-service",
    "payment-service",
    "inventory-service",
    "warehouse-service",
    "promo-service",
    "notification-service",
    "search-service",
    "product-service",
]

SERVICE_MAP = {f"services/{s}": s for s in SERVICES}

ROUTES: dict[str, list[tuple[str, str]]] = {
    "api-gateway": [("GET", "/orders"), ("POST", "/checkout"), ("GET", "/me"), ("GET", "/search")],
    "auth-service": [("POST", "/login")],
    "user-service": [("GET", "/me"), ("GET", "/profile")],
    "order-service": [("GET", "/orders"), ("POST", "/checkout")],
    "payment-service": [("POST", "/pay"), ("GET", "/charge")],
    "inventory-service": [("GET", "/stock")],
    "warehouse-service": [("POST", "/ship"), ("GET", "/shipments")],
    "promo-service": [("GET", "/validate"), ("GET", "/offers")],
    "notification-service": [("POST", "/notify")],
    "search-service": [("GET", "/search")],
    "product-service": [("GET", "/products")],
}

# (src, dst, method, route, traffic_volume, error_rate)
RUNTIME_EDGES: list[tuple[str, str, str, str, float, float]] = [
    ("api-gateway", "order-service", "GET", "/orders", 0.9, 0.01),
    ("api-gateway", "order-service", "POST", "/checkout", 0.7, 0.02),
    ("api-gateway", "user-service", "GET", "/me", 0.8, 0.005),  # runtime-only
    ("api-gateway", "auth-service", "POST", "/login", 0.6, 0.01),  # runtime-only
    ("api-gateway", "search-service", "GET", "/search", 0.5, 0.03),
    ("order-service", "promo-service", "GET", "/validate", 0.7, 0.22),  # runtime-only
    ("order-service", "warehouse-service", "POST", "/ship", 0.3, 0.05),
    ("search-service", "product-service", "GET", "/products", 0.9, 0.01),
]

# (src, dst, topic, traffic_volume, error_rate)
MQ_EDGES: list[tuple[str, str, str, float, float]] = [
    ("order-service", "notification-service", "orders.created", 0.4, 0.08),
]

REPO_FILES: dict[str, str] = {
    "services/api-gateway/app.py": """
from order_service import OrderClient
from search_service import SearchClient

app = None  # framework stub

@app.get("/orders")
def list_orders():
    return OrderClient().list()

@app.post("/checkout")
def checkout():
    return OrderClient().checkout()

@app.get("/me")
def me():
    # resolved through service discovery at runtime
    return {"ok": True}

@app.get("/search")
def search():
    return SearchClient().search()
""",
    "services/api-gateway/order_service.py": "class OrderClient:\n    pass\n",
    "services/api-gateway/search_service.py": "class SearchClient:\n    pass\n",
    "services/auth-service/app.py": """
@app.post("/login")
def login():
    return {"token": "x"}
""",
    "services/user-service/app.py": """
@app.get("/me")
def me():
    return {"user": None}

@app.get("/profile")
def profile():
    return {"profile": None}
""",
    "services/order-service/app.py": """
import os
import payment_service
import inventory_service
import warehouse_service

@app.get("/orders")
def list_orders():
    return {"orders": []}

@app.post("/checkout")
def checkout():
    inv = inventory_service.stock()
    promo = validate_promo()  # runtime-discovered dependency
    warehouse_service.ship()
    return {"ok": True}

def validate_promo():
    url = os.environ.get("PROMO_URL")  # dynamic URL: invisible to static analysis
    return {"promo": url}
""",
    "services/order-service/payment_service.py": "class PaymentClient:\n    pass\n",
    "services/order-service/inventory_service.py": "class InventoryClient:\n    pass\n",
    "services/order-service/warehouse_service.py": "class WarehouseClient:\n    pass\n",
    "services/payment-service/app.py": """
@app.post("/pay")
def pay():
    return {"paid": True}

@app.get("/charge")
def charge():
    return {"amount": 0}
""",
    "services/inventory-service/app.py": """
import warehouse_service

@app.get("/stock")
def stock():
    return warehouse_service.shipments()
""",
    "services/inventory-service/warehouse_service.py": "class WarehouseClient:\n    pass\n",
    "services/warehouse-service/app.py": """
@app.post("/ship")
def ship():
    return {"shipped": True}

@app.get("/shipments")
def shipments():
    return []
""",
    "services/promo-service/app.py": """
@app.get("/validate")
def validate():
    return {"valid": True}

@app.get("/offers")
def offers():
    return []
""",
    "services/notification-service/app.py": """
@app.post("/notify")
def notify():
    return {"sent": True}
""",
    "services/search-service/app.py": """
from product_service import ProductClient

@app.get("/search")
def search():
    return ProductClient().search()
""",
    "services/search-service/product_service.py": "class ProductClient:\n    pass\n",
    "services/product-service/app.py": """
@app.get("/products")
def products():
    return []
""",
}


def build_full_graph() -> HybridGraph:
    """Hybrid graph: static import edges + runtime call edges + MQ edges."""
    from impact_engine.static_parser import parse_repository

    graph = HybridGraph()
    analysis = parse_repository(dict(REPO_FILES), SERVICE_MAP)
    for src, dst in analysis.edges:
        graph.add_edge(src, dst, EdgeKind.STATIC_IMPORT)
    for svc in SERVICES:
        graph.add_service(svc)
        for method, route in ROUTES[svc]:
            graph.add_endpoint(svc, method, route)
    for src, dst, method, route, traffic, err in RUNTIME_EDGES:
        graph.add_edge(
            src,
            dst,
            EdgeKind.RUNTIME_CALL,
            weight=min(1.0, traffic * (1.0 + err * 10.0)),
            traffic_volume=traffic,
            call_frequency=traffic * 100.0,
            error_rate=err,
            route=route,
            method=method,
        )
    for src, dst, topic, traffic, err in MQ_EDGES:
        graph.add_edge(
            src,
            dst,
            EdgeKind.MESSAGE_QUEUE,
            weight=min(1.0, traffic * (1.0 + err * 10.0)),
            traffic_volume=traffic,
            call_frequency=traffic * 100.0,
            error_rate=err,
            route=f"topic:{topic}",
            method="PUB",
        )
    return graph


def build_static_only_graph() -> HybridGraph:
    """Graph a pure static analyzer would construct from REPO_FILES."""
    from impact_engine.static_parser import parse_repository

    graph = HybridGraph()
    analysis = parse_repository(dict(REPO_FILES), SERVICE_MAP)
    for src, dst in analysis.edges:
        graph.add_edge(src, dst, EdgeKind.STATIC_IMPORT)
    for svc in SERVICES:
        graph.add_service(svc)
    return graph


def truth_set(
    graph: HybridGraph, changed_services: list[str]
) -> set[str]:
    """Ground-truth affected services: impact radius over the FULL graph.

    A production failure in service S degrades S, every caller that reaches
    it (reverse reachability) and everything S itself depends on along its
    call paths (forward reachability). This is the runtime-aware oracle the
    predictors are scored against.
    """
    from impact_engine.risk import impact_radius

    return {
        blast.service
        for blast in impact_radius(graph, changed_services)
    }


def truth_broken_endpoints(graph: HybridGraph, changed_services: list[str]) -> list[tuple[str, str, str]]:
    """Caller endpoints that break when a changed service's route changes."""
    broken: set[tuple[str, str, str]] = set()
    for edge in graph.runtime_edges():
        if edge.dst in changed_services and edge.route.startswith("/"):
            broken.add((edge.src, edge.method, edge.route))
    return sorted(broken)