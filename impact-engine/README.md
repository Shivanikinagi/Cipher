# Runtime-Aware Change Impact Engine

Predict the blast radius of a code change (GitHub PR) across a microservice
architecture by combining **static dependency analysis** with **runtime
telemetry** (OpenTelemetry traces/metrics) and **historical incidents**.

Static-only tools (Depwire / cipher-cli style) reconstruct dependencies from
source code and miss everything that only exists at runtime: service
discovery, env-configured URLs, dynamic dispatch, message queues, and the
traffic/error patterns that determine whether an edge actually matters.

## Core hypothesis

> Combining static dependency analysis with runtime telemetry (traces,
> metrics, and historical incidents) improves the accuracy of change-impact
> prediction compared to static analysis alone.

The benchmark in this repo measures it: **75.4% recall static-only vs 100%
hybrid (+32.5%)** on 60 controlled change scenarios
([latest report](benchmark/report.md)).

## What it does

Given a PR, it predicts:

- which **services are affected** (risk ranked High/Medium/Low)
- which **endpoints/APIs break** (callers of changed routes)
- an **AI-generated explanation** per prediction

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate                    # Windows; or source .venv/bin/activate
pip install -e ".[dev]"

# 1. run the benchmark (writes benchmark/report.md + results/results.json)
python -m benchmark.run

# 2. serve the API (boots with the demo ecosystem pre-seeded)
python -m uvicorn impact_engine.main:app --port 8000

# 3. stream demo telemetry into it (1400+ spans)
python -m demo.simulate

# 4. analyze a PR
curl -s -X POST http://127.0.0.1:8000/analyze -H "Content-Type: application/json" -d '{
  "pr_identifier": "acme/commerce#1205",
  "change_description": "relax promo eligibility rules, fix /validate contract",
  "changed_files": [{"path": "services/promo-service/app.py", "content": "@app.get(\"/validate\")\ndef validate(): pass"}]
}'
```

## API surface

| Method | Path | Purpose |
|---|---|---|
| POST | `/analyze` | direct analysis request (changed files + description) |
| POST | `/webhook/github` | GitHub `pull_request` webhook body |
| POST | `/analyze-pr?repo=&number=` | fetch PR diff from GitHub API (`GITHUB_TOKEN`) |
| POST | `/v1/traces` | OTLP/HTTP JSON trace batches (per-service `resourceSpans`) |
| POST | `/v1/metrics` | metric points |
| POST | `/telemetry/apply` | fold ingested telemetry into the hybrid graph |
| GET/POST | `/incidents` | historical incidents (JSON store) |
| GET | `/graph` | combined hybrid graph (static + runtime + MQ edges) |
| GET | `/health` | liveness |

## Pipeline (LangGraph-ready stages)

```
[diff] -> parse   -> predict  -> score   -> explain -> response
           file      hybrid     risk +     template
           -> svc    blast      incident   (+ optional LLM)
           routes    radius     sim       summary
```

Each stage is a pure function on `ImpactEngine` (`src/impact_engine/engine.py`)
so it can be lifted into LangGraph agents later.

## How edges get into the hybrid graph

- **static_import** — Python parsed with `ast` (accurate), TS/JS and Java via
  regex extractors; files mapped to services by directory prefixes or
  normalized module/service name matching.
- **runtime_call** — server spans paired with their client-span parents across
  services; edge weight folds in call frequency, traffic volume and error
  rate (`src/impact_engine/telemetry.py`).
- **message_queue** — async contracts (topic edges), invisible to code.

Blast radius is a weighted bidirectional traversal
(`src/impact_engine/risk.py): changing S affects both the services that call
S (reverse reachability — the API breaks) and the services S calls (forward
reachability). Risk = 0.45 blast + 0.25 traffic + 0.15 error + 0.15 incident
similarity, thresholded High >= 0.7 / Medium >= 0.4.

## Benchmarking

`benchmark/scenarios.py` generates controlled changes over the demo
ecosystem; ground truth is the runtime-aware oracle (impact radius over the
full hybrid graph). `benchmark/run.py` scores the **static-only predictor**
(import edges only) vs the **hybrid engine** on precision/recall/F1 per
scenario and micro-averaged, failing (exit 1) unless hybrid recall exceeds the
static baseline by > 20% (the project's success metric).

## Layout

```
src/impact_engine/    engine, graph, static parser, telemetry, risk,
                      incidents, explanations, GitHub webhook, FastAPI app
demo/                 synthetic microservice ecosystem + trace simulator
benchmark/            scenario generator + precision/recall harness
tests/                pytest suite (unit + API + benchmark sanity)
docs/architecture.md  design decisions and trade-offs
```

## Configuration (env)

- `GITHUB_TOKEN` — REST API PR diffs
- `IMPACT_ENGINE_LLM_URL` / `IMPACT_ENGINE_LLM_API_KEY` / `IMPACT_ENGINE_LLM_MODEL`
  — optional OpenAI-compatible endpoint for the AI summary (templated
  explanations are used when unset)
- `IMPACT_ENGINE_INCIDENTS_PATH` — incident JSON store location