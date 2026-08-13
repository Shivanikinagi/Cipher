"""Benchmark: static-only baseline vs hybrid engine.

Measures per-scenario precision / recall / F1 of predicted affected
services against the runtime-aware ground-truth oracle, aggregates
micro-averaged metrics, and writes ``benchmark/report.md`` +
``benchmark/results/results.json``. Exit code 0 when the hybrid engine
beats the static baseline on recall by > 20% (success metric).
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from impact_engine.engine import ImpactEngine
from impact_engine.graph import HybridGraph
from impact_engine.incidents import IncidentStore
from impact_engine.models import EdgeKind

from demo.ecosystem import (
    SERVICE_MAP,
    build_full_graph,
    build_static_only_graph,
)

from .scenarios import (
    default_scenario_descriptions,
    generate_scenarios,
    scenario_request,
)

BENCHMARK_DIR = Path(__file__).resolve().parent
RESULTS_DIR = BENCHMARK_DIR / "results"


def _predicted_set(result) -> set[str]:
    return {a.service for a in result.affected_services}


def _metrics(pred: set[str], truth: set[str]) -> dict:
    tp = len(pred & truth)
    fp = len(pred - truth)
    fn = len(truth - pred)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1}


def _aggregate(metrics_list: list[dict]) -> dict:
    tp = sum(m["tp"] for m in metrics_list)
    fp = sum(m["fp"] for m in metrics_list)
    fn = sum(m["fn"] for m in metrics_list)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


async def _run_predictor(
    engine: ImpactEngine, scenarios: list, descriptions: list[str]
) -> list[dict]:
    results = []
    for s, desc in zip(scenarios, descriptions):
        result = await engine.analyze(scenario_request(s, desc))
        pred = _predicted_set(result)
        results.append(_metrics(pred, s.truth_services))
    return results


def run_benchmark(cases: int = 60, seed: int = 42) -> int:
    print(f"generating {cases} controlled change scenarios (seed={seed})")
    scenarios = generate_scenarios(cases, seed)

    full_graph = build_full_graph()
    static_graph = build_static_only_graph()

    hybrid_engine = ImpactEngine(full_graph, IncidentStore(), SERVICE_MAP, llm_enabled=False)
    static_engine = ImpactEngine(static_graph, IncidentStore(), SERVICE_MAP, llm_enabled=False)

    descriptions = default_scenario_descriptions(scenarios)
    t0 = time.perf_counter()
    hybrid_metrics = asyncio.run(_run_predictor(hybrid_engine, scenarios, descriptions))
    t_hybrid = time.perf_counter() - t0
    t0 = time.perf_counter()
    static_metrics = asyncio.run(_run_predictor(static_engine, scenarios, descriptions))
    t_static = time.perf_counter() - t0

    agg_hybrid = _aggregate(hybrid_metrics)
    agg_static = _aggregate(static_metrics)

    recall_improvement = (agg_hybrid["recall"] - agg_static["recall"]) / max(agg_static["recall"], 1e-9)

    report = _render_report(
        cases, seed, agg_hybrid, agg_static, recall_improvement,
        t_hybrid, t_static, scenarios, hybrid_metrics, static_metrics,
    )
    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / "results.json").write_text(
        json.dumps(
            {
                "cases": cases,
                "seed": seed,
                "hybrid": agg_hybrid,
                "static": agg_static,
                "recall_improvement_pct": round(recall_improvement * 100, 1),
                "latency_s": {
                    "hybrid_total": round(t_hybrid, 3),
                    "static_total": round(t_static, 3),
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (BENCHMARK_DIR / "report.md").write_text(report, encoding="utf-8")
    print(report)
    return 0 if recall_improvement > 0.2 else 1


def _render_report(
    cases, seed, agg_hybrid, agg_static, recall_improvement, t_hybrid, t_static,
    scenarios, hybrid_metrics, static_metrics,
) -> str:
    lines = [
        "# Benchmark Report",
        "",
        f"- scenarios: {cases} (seed={seed})",
        f"- oracle: full hybrid graph (static + runtime + MQ) reachability",
        "",
        "## Aggregated (micro-averaged over all scenarios)",
        "",
        "| predictor | precision | recall | F1 | tp | fp | fn |",
        "|---|---|---|---|---|---|---|",
        f"| static-only (baseline) | {agg_static['precision']:.3f} | {agg_static['recall']:.3f} | {agg_static['f1']:.3f} | {agg_static['tp']} | {agg_static['fp']} | {agg_static['fn']} |",
        f"| **hybrid (this engine)** | {agg_hybrid['precision']:.3f} | {agg_hybrid['recall']:.3f} | {agg_hybrid['f1']:.3f} | {agg_hybrid['tp']} | {agg_hybrid['fp']} | {agg_hybrid['fn']} |",
        "",
        f"**Recall improvement over static baseline: {recall_improvement * 100:.1f}%** "
        f"(success metric target: > 20%)",
        f"- latency: hybrid {t_hybrid:.2f}s total, static {t_static:.2f}s total "
        f"(~{t_hybrid / max(cases, 1) * 1000:.0f} ms/scenario)",
        "",
        "## Why recall improves",
        "",
        "The static baseline can only traverse import edges. Runtime-only edges"
        " (service discovery, env-configured URLs, message queues) are invisible"
        " to it, so changes in `promo-service`, `user-service`, `auth-service`"
        " produce near-zero baseline recall while the hybrid engine predicts"
        " the real caller chains.",
        "",
        "## Per-scenario detail",
        "",
        "| id | changed service | truth size | static p/r | hybrid p/r |",
        "|---|---|---|---|---|",
    ]
    for s, sm, hm in zip(scenarios, static_metrics, hybrid_metrics):
        svc = ",".join(s.changed_services) if s.changed_services else "(infra)"
        lines.append(
            f"| {s.id} | {svc} | {len(s.truth_services)} | "
            f"{sm['precision']:.2f}/{sm['recall']:.2f} | {hm['precision']:.2f}/{hm['recall']:.2f} |"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    import sys

    sys.exit(run_benchmark())