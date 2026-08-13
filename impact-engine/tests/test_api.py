import pytest
from fastapi.testclient import TestClient

from impact_engine.main import app, state

client = TestClient(app)


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_analyze_endpoint():
    resp = client.post(
        "/analyze",
        json={
            "pr_identifier": "test#1",
            "change_description": "fix promo validation",
            "changed_files": [
                {"path": "services/promo-service/app.py", "content": "@app.get('/validate')\ndef validate(): pass\n"},
                {"path": "services/order-service/app.py", "content": "@app.post('/checkout')\ndef checkout(): pass\n"},
            ],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["latency_ms"] < 30000
    services = {a["service"] for a in body["prediction"]["affected_services"]}
    assert "promo-service" in services


def test_webhook_unsupported_event_rejected():
    resp = client.post("/webhook/github", json={"action": "closed", "pull_request": {}})
    assert resp.status_code == 422


def test_webhook_opened_event():
    resp = client.post(
        "/webhook/github",
        json={
            "action": "opened",
            "pull_request": {
                "number": 42,
                "title": "refactor checkout",
                "body": "add idempotency",
                "head": {"ref": "feature/x"},
                "files": [{"filename": "services/order-service/app.py"}],
            },
            "repository": {"full_name": "acme/store"},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["pr_identifier"] in ("acme/store#42", "")


def test_traces_ingestion_and_apply():
    resp = client.post(
        "/v1/traces",
        json={
            "resourceSpans": [
                {
                    "resource": {"attributes": {"service.name": "api-gateway"}},
                    "scopeSpans": [
                        {
                            "scope": {"name": "x"},
                            "spans": [
                                {
                                    "traceId": "t1",
                                    "spanId": "g1",
                                    "parentSpanId": None,
                                    "name": "GET /a",
                                    "kind": "SPAN_KIND_SERVER",
                                    "status": {"code": "STATUS_CODE_OK"},
                                    "attributes": {"http.route": "/a", "http.method": "GET"},
                                    "startTimeUnixNano": 0,
                                    "endTimeUnixNano": 1000000,
                                },
                                {
                                    "traceId": "t1",
                                    "spanId": "gc1",
                                    "parentSpanId": "g1",
                                    "name": "HTTP GET /b",
                                    "kind": "SPAN_KIND_CLIENT",
                                    "status": {"code": "STATUS_CODE_OK"},
                                    "attributes": {"http.route": "/b", "http.method": "GET"},
                                    "startTimeUnixNano": 0,
                                    "endTimeUnixNano": 1000000,
                                },
                            ],
                        }
                    ],
                },
                {
                    "resource": {"attributes": {"service.name": "order-service"}},
                    "scopeSpans": [
                        {
                            "scope": {"name": "x"},
                            "spans": [
                                {
                                    "traceId": "t1",
                                    "spanId": "s1",
                                    "parentSpanId": "gc1",
                                    "name": "GET /b",
                                    "kind": "SPAN_KIND_SERVER",
                                    "status": {"code": "STATUS_CODE_ERROR"},
                                    "attributes": {"http.route": "/b", "http.method": "GET"},
                                    "startTimeUnixNano": 0,
                                    "endTimeUnixNano": 4000000,
                                }
                            ],
                        }
                    ],
                },
            ]
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    apply = client.post("/telemetry/apply")
    assert apply.status_code == 200
    applied = apply.json()
    assert applied["runtime_edges"] >= body["runtime_edges_added"] >= 1


def test_incidents_crud():
    resp = client.post(
        "/incidents",
        json={
            "summary": "checkout broke after idempotency change",
            "affected_services": ["order-service"],
            "tags": ["contract"],
            "text": "rollback restored service",
        },
    )
    assert resp.status_code == 200
    listed = client.get("/incidents").json()["incidents"]
    assert any("idempotency" in i["summary"].lower() for i in listed)