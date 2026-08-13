from impact_engine.static_parser import (
    _python_imports,
    _ts_imports,
    _java_imports,
    parse_repository,
    resolve_service,
)


def test_python_imports_ast():
    src = """
import os
import payment_client
from inventory_client import InventoryClient
from .local import thing
from warehouse_client.sub import Ship
"""
    mods = _python_imports(src)
    assert "os" in mods
    assert "payment_client" in mods
    assert "inventory_client" in mods
    assert "warehouse_client.sub" in mods
    assert not any(m.startswith(".") for m in mods)


def test_ts_imports():
    src = '''
import { z } from "zod";
const x = require("express");
import type { T } from "./local";
'''
    mods = _ts_imports(src)
    assert "zod" in mods
    assert "express" in mods


def test_java_imports():
    src = "package com.acme;\nimport java.util.List;\nimport com.acme.orders.Order;\n"
    assert "java.util.List" in _java_imports(src)
    assert "com.acme.orders.Order" in _java_imports(src)


def test_resolve_service_matches_prefix():
    svc_map = {"services/order-service": "order-service", "services/payment-service": "payment-service"}
    assert resolve_service("services/order-service/app.py", svc_map) == "order-service"
    assert resolve_service("services/order-service/api/v1/handlers.py", svc_map) == "order-service"
    assert resolve_service("other/file.py", svc_map, lambda p: "fallback") == "fallback"


def test_parse_repository_builds_cross_service_edges():
    files = {
        "services/order-service/app.py": "import payment_service\nimport inventory_service\n",
        "services/order-service/local.py": "x = 1\n",
        "services/payment-service/app.py": "import os\n",
    }
    svc_map = {
        "services/order-service": "order-service",
        "services/payment-service": "payment-service",
        "services/inventory-service": "inventory-service",
    }
    analysis = parse_repository(files, svc_map)
    assert ("order-service", "payment-service") in analysis.edges
    assert ("order-service", "inventory-service") in analysis.edges
    assert not any(
        src == dst or "services" in (src, dst) for src, dst in analysis.edges
    )


def test_parse_repository_route_definitions():
    files = {"services/api-gateway/app.py": '@app.get("/orders")\ndef list_orders(): pass\n'}
    analysis = parse_repository(files, {"services/api-gateway": "api-gateway"})
    assert ("route", "/orders") in analysis.definition_points["services/api-gateway/app.py"]