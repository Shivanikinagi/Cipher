"""FastAPI application: REST API + GitHub webhook + OTLP ingestion."""
from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse

from .engine import ImpactEngine
from .graph import HybridGraph
from .github_pr import fetch_pr_files, parse_webhook, pr_identifier
from .incidents import IncidentStore
from .models import (
    AnalysisRequest,
    AnalysisResponse,
    Incident,
    MetricBatch,
    TraceBatch,
)
from .telemetry import RuntimeTelemetry, apply_edges_to_graph


class AppState:
    def __init__(self, seed_demo: bool = True) -> None:
        from demo.ecosystem import SERVICE_MAP as DEMO_SERVICE_MAP
        from demo.ecosystem import build_full_graph

        self.graph = HybridGraph()
        if seed_demo:
            self.graph.merge(build_full_graph())
        self.telemetry = RuntimeTelemetry()
        incidents_path = os.environ.get("IMPACT_ENGINE_INCIDENTS_PATH")
        self.incidents = IncidentStore(incidents_path or "data/incidents.json")
        self.engine = ImpactEngine(
            self.graph, self.incidents, DEMO_SERVICE_MAP if seed_demo else {}
        )


state = AppState()


@asynccontextmanager
async def lifespan(_: FastAPI):
    state.incidents.load()
    yield


app = FastAPI(
    title="Runtime-Aware Change Impact Engine",
    version="0.1.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# analysis API
# ---------------------------------------------------------------------------
@app.post("/analyze", response_model=AnalysisResponse)
async def analyze(request: AnalysisRequest):
    t0 = time.perf_counter()
    result = await state.engine.analyze(request)
    latency = (time.perf_counter() - t0) * 1000
    return AnalysisResponse(
        pr_identifier=request.pr_identifier or "local",
        prediction=result,
        latency_ms=round(latency, 1),
    )


@app.post("/webhook/github")
async def webhook_github(request_raw: Request, body: dict | None = None):
    """GitHub webhook entrypoint (pull_request events).

    Accepts a raw webhook body; returns 422 for unsupported events.
    """
    payload = body if body is not None else await request_raw.json()
    req = parse_webhook(payload or {})
    if req is None:
        raise HTTPException(status_code=422, detail="Unsupported or incomplete webhook event")
    t0 = time.perf_counter()
    result = await state.engine.analyze(req)
    latency = (time.perf_counter() - t0) * 1000
    return AnalysisResponse(
        pr_identifier=req.pr_identifier or "gh-webhook",
        prediction=result,
        latency_ms=round(latency, 1),
    )


@app.post("/analyze-pr")
async def analyze_pr(repo: str, number: int):
    """Fetch a PR's changed files from GitHub and analyze it."""
    files = await fetch_pr_files(repo, number)
    req = AnalysisRequest(changed_files=files, change_description=f"PR {repo}#{number}")
    t0 = time.perf_counter()
    result = await state.engine.analyze(req)
    latency = (time.perf_counter() - t0) * 1000
    return AnalysisResponse(
        pr_identifier=pr_identifier(repo, number),
        prediction=result,
        latency_ms=round(latency, 1),
    )


# ---------------------------------------------------------------------------
# OpenTelemetry ingestion
# ---------------------------------------------------------------------------
@app.post("/v1/traces")
async def ingest_traces(request: Request):
    raw = await request.json()
    body = TraceBatch(**raw)
    spans = []
    for rs in body.resource_spans:
        svc = ((rs.get("resource") or {}).get("attributes") or {}).get("service.name", "")
        for scope in rs.get("scopeSpans") or []:
            for s in scope.get("spans") or []:
                spans.append(
                    {
                        "trace_id": s.get("traceId", ""),
                        "span_id": s.get("spanId", ""),
                        "parent_span_id": s.get("parentSpanId"),
                        "service_name": s.get("serviceName") or svc,
                        "name": s.get("name", ""),
                        "kind": s.get("kind", "SPAN_KIND_SERVER"),
                        "status": (s.get("status") or {}).get(
                            "code", "STATUS_CODE_OK"
                        ),
                        "attributes": s.get("attributes") or {},
                        "start_time_unix_nano": s.get("startTimeUnixNano", 0),
                        "end_time_unix_nano": s.get("endTimeUnixNano", 0),
                    }
                )
    added = state.telemetry.ingest_spans(spans)
    return {"ingested_spans": len(spans), "runtime_edges_added": added}


@app.post("/v1/metrics")
def ingest_metrics(body: MetricBatch):
    state.telemetry.ingest_metrics(body.metrics)
    return {"ingested_metrics": len(body.metrics)}


@app.post("/telemetry/apply")
def apply_telemetry():
    edges = state.telemetry.to_edges()
    added = apply_edges_to_graph(state.graph, edges)
    return {"runtime_edges": added, "stats": state.telemetry.edge_stats()}


# ---------------------------------------------------------------------------
# incidents
# ---------------------------------------------------------------------------
@app.get("/incidents")
def list_incidents():
    return {"incidents": [i.model_dump() for i in state.incidents.all()]}


@app.post("/incidents")
def add_incident(incident: Incident):
    return state.incidents.add(incident)


# ---------------------------------------------------------------------------
# graph introspection
# ---------------------------------------------------------------------------
@app.get("/graph")
def get_graph():
    return state.graph.to_dict()


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok", "version": "0.1.0"}