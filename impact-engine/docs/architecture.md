# Architecture: Runtime-Aware Change Impact Engine

## 1. System overview

```
 GitHub PR / webhook          OTLP traces/metrics          incident reports
      |                            |                            |
      v                            v                            v
+----------------------------------+----------------------------+
|                         Impact Engine (FastAPI)              |
|  parse  (files -> services, changed routes)                  |
|  predict (hybrid blast radius + broken endpoint detection)   |
|  score  (risk = blast/traffic/error/incident)                |
|  explain(templated narrative + optional LLM summary)         |
+--------------------------------------------------------------+
   HybridGraph: static_import + runtime_call + message_queue
```

The MVP is a single FastAPI process with in-memory state and a JSON incident
store: no external databases, so it runs anywhere. The orchestrator stages
are pure functions on `ImpactEngine`, sized to be lifted into LangGraph agents
(planned: `parse_agent`, `predict_agent`, `score_agent`, `explain_agent`).

## 2. Hybrid graph construction

**Static layer** (`static_parser.py`)

- Python imports via `ast` (accurate, handles renames/relative imports).
- TypeScript/JavaScript `import`/`require` and Java `import` via regex.
- Files map to owning services by longest directory prefix in the service
  map, then by *normalized name match* (`payment_service` -> `payment-service`),
  which handles client SDK modules without extra config.

**Runtime layer** (`telemetry.py`)

- Ingests OTLP/HTTP JSON (per-service `resourceSpans`, the way real exporters
  send it).
- Edges are reconstructed by pairing a child **server** span with its parent
  **client** span across services (parent's service -> child's service).
- Edge attributes: `call_frequency` (calls per window), `traffic_volume`
  (share of max edge traffic), `error_rate`, and the observed HTTP route.
- Edge weight `= min(1, freq * (1 + 10*error_rate) / 50)` so hot, flaky
  contracts propagate further in blast radius.

**Queue layer** — `message_queue` edges model async contracts (topic edges)
that are invisible to code.

Design choice: edges are keyed `(src, dst, kind, route)`, so `GET /orders`
and `POST /checkout` between the same pair are separate contracts with
separate traffic — required for route-level broken-endpoint detection.

**Trade-off:** two-process ingestion. `POST /v1/traces` buffers spans;
`POST /telemetry/apply` folds them into the graph (dedup/merge). This mirrors
"export from the collector, materialize on a schedule" in production and
keeps the analysis graph stable between refreshes.

## 3. Blast radius

`risk.py` walks the graph bidirectionally with a multiplicative decay
(Dijkstra-style best-first, score `= edge_weight * decay ** distance`):

- **forward** — services S depends on (payload/usage change propagates down)
- **reverse** — services that call S (the API contract breaks)

Impact set = union. Both directions carry runtime weights, so a hot path
propagates farther than a rarely used one. This is the key divergence from
static-only tools, which traverse unweighted import edges in one direction.

## 4. Risk scoring

```
risk = 0.45 * blast_score
     + 0.25 * traffic_volume      (share of observed traffic on incoming edges)
     + 0.15 * error_rate          (flaky edge = more likely to break)
     + 0.15 * incident_similarity (token/bigram Jaccard + affected-service overlap)
High >= 0.70, Medium >= 0.40, else Low
```

Weights are deliberately documented constants (configurable in code) so the
benchmark is reproducible; they were tuned for the demo ecosystem, not
fitted to the benchmark.

Broken-endpoint detection: a runtime edge whose `route` matches a changed
route on the callee flags the caller's endpoint as broken with the edge's
traffic as the severity signal.

## 5. Explanations

Deterministic templated narratives (offline, every prediction gets one) with
an optional LLM layer: if `IMPACT_ENGINE_LLM_URL` is set, an OpenAI-compatible
chat completion summarizes the prediction and is prepended. The LLM call is
best-effort, never blocks, never changes the scored result — it only adds
prose for humans.

## 6. Benchmarking methodology

`benchmark/scenarios.py` generates controlled changes: one mutated service
(+ its route) or an infra-only file, seeded RNG for reproducibility.

Ground truth is a **runtime-aware oracle** — impact radius over the *full*
hybrid graph. This is the controlled, defendable position: we know exactly
which edges exist at runtime because we authored the ecosystem; the static
baseline predictor operates on the identical code but only sees import edges.

Scoring: per-scenario precision/recall/F1 micro-averaged. The run fails
(exit 1) unless hybrid recall exceeds static recall by > 20% — the project
success metric. Latency budget (< 30 s/analysis) is trivially met in-memory
(~5 ms); with Neo4j/TimescaleDB this becomes the time budget for the graph
query, not the engine.

**Known limitation:** hybrid precision/recall vs the oracle is a best case —
prediction and truth derive from the same graph. The honest claim is the
*delta vs the static baseline* (32.5% recall), and extending the oracle to an
independent failure-simulation (observed error propagation) is listed as the
first next step below.

## 7. Productionization path (planned)

| Component | MVP (this repo) | Target |
|---|---|---|
| Graph | in-memory `HybridGraph` | Neo4j (Cypher traversal, weighted) |
| Time series | buffered spans | TimescaleDB (rolling windows, anomaly spikes) |
| Incident similarity | token/bigram on JSON store | ChromaDB embeddings |
| Orchestration | stage functions | LangGraph agents with tool use |
| Observability | none | OpenTelemetry Collector on the engine itself |
| CI/CD | pytest on GitHub Actions + Dockerfile | full pipeline |

## 8. Reproducibility

```bash
pip install -e ".[dev]"
pytest -q                 # 34 tests: parser, graph, telemetry, risk, engine, API, benchmark
python -m benchmark.run   # deterministic (seed=42), writes report.md + results.json
```