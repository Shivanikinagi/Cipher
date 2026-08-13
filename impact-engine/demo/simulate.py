"""Stream realistic OTLP traces/metrics from the demo ecosystem into a
running impact-engine server, then apply them to the hybrid graph.

Usage: ``python -m demo.simulate`` or ``impact-engine simulate``.
"""
from __future__ import annotations

import asyncio
import random

import httpx

from .ecosystem import RUNTIME_EDGES, build_full_graph

TRACE_BATCH_SIZE = 40
REQUESTS = 140
BASE_URL = "http://127.0.0.1:8000"


def _span(
    trace_id: str,
    span_id: str,
    parent_span_id: str | None,
    service: str,
    name: str,
    kind: str,
    attrs: dict,
    t: float,
    duration_ms: float,
    error: bool,
) -> dict:
    end_ns = int(t * 1e9)
    start_ns = int((t - duration_ms / 1000.0) * 1e9)
    return {
        "traceId": trace_id,
        "spanId": span_id,
        "parentSpanId": parent_span_id,
        "serviceName": service,
        "name": name,
        "kind": kind,
        "status": {"code": "STATUS_CODE_ERROR" if error else "STATUS_CODE_OK"},
        "attributes": attrs,
        "startTimeUnixNano": start_ns,
        "endTimeUnixNano": end_ns,
    }


def generate_trace_batches(seed: int = 7, requests: int = REQUESTS) -> list[dict]:
    rng = random.Random(seed)
    graph = build_full_graph()
    edges_by_src: dict[str, list[tuple]] = {}
    for src, dst, method, route, traffic, err in RUNTIME_EDGES:
        edges_by_src.setdefault(src, []).append((dst, method, route, traffic, err))

    t = 1000.0
    batches: list[dict] = []
    spans: list[dict] = []
    for _ in range(requests):
        trace_id = f"trace-{rng.randint(0, 10**9):010x}"
        duration = rng.uniform(10, 90)

        def emit(parent: str, svc: str, method: str, route: str, err: float, name: str) -> None:
            nonlocal t
            error = rng.random() < err
            ms = duration if not error else duration * rng.uniform(2.0, 6.0)
            spans.append(
                _span(
                    trace_id,
                    f"{svc}-{len(spans)}",
                    parent,
                    svc,
                    name,
                    "SPAN_KIND_SERVER",
                    {"http.method": method, "http.route": route},
                    t,
                    ms,
                    error,
                )
            )
            t += ms / 1000.0

        # client spans (SPAN_KIND_CLIENT) for edge attribution
        def client(parent: str, svc: str, child_svc: str, method: str, route: str) -> str:
            span_id = f"{child_svc}-client-{len(spans)}"
            spans.append(
                _span(
                    trace_id,
                    span_id,
                    parent,
                    svc,
                    f"HTTP {method} {route}",
                    "SPAN_KIND_CLIENT",
                    {"http.method": method, "http.route": route},
                    t,
                    duration,
                    False,
                )
            )
            return span_id

        gw = f"gateway-server-{len(spans)}"
        spans.append(
            _span(
                trace_id,
                gw,
                None,
                "api-gateway",
                "HTTP GET /orders",
                "SPAN_KIND_SERVER",
                {"http.method": "GET", "http.route": "/orders"},
                t,
                duration,
                False,
            )
        )
        for dst, method, route, traffic, err in edges_by_src.get("api-gateway", []):
            if rng.random() > traffic:
                continue
            cid = client(gw, "api-gateway", dst, method, route)
            emit(cid, dst, method, route, err, f"HTTP {method} {route}")
            # downstream fan-out for order-service
            if dst == "order-service" and method == "GET":
                for d2, m2, r2, tr2, e2 in edges_by_src.get("order-service", []):
                    if rng.random() > tr2:
                        continue
                    c2 = client(cid, "order-service", d2, m2, r2)
                    emit(c2, d2, m2, r2, e2, f"HTTP {m2} {r2}")

        if len(spans) >= TRACE_BATCH_SIZE:
            batches.append(_envelope(spans))
            spans = []
    if spans:
        batches.append(_envelope(spans))
    return batches


def _envelope(spans: list[dict]) -> dict:
    """Bucket spans into per-service resourceSpans blocks (OTLP convention)."""
    by_service: dict[str, list[dict]] = {}
    for s in spans:
        by_service.setdefault(s["serviceName"], []).append(s)
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": {"service.name": svc}},
                "scopeSpans": [{"scope": {"name": "impact-engine-demo"}, "spans": svc_spans}],
            }
            for svc, svc_spans in sorted(by_service.items())
        ]
    }


async def main(base_url: str = BASE_URL) -> None:
    async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
        total = 0
        for batch in generate_trace_batches():
            resp = await client.post("/v1/traces", json=batch)
            resp.raise_for_status()
            total += resp.json().get("ingested_spans", 0)
        apply = await client.post("/telemetry/apply")
        apply.raise_for_status()
        stats = apply.json()
        print(f"ingested {total} spans; graph now has {stats['runtime_edges']} runtime edges")
        print(stats["stats"])


if __name__ == "__main__":
    asyncio.run(main())