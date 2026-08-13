"""Controlled change-scenario generator for the benchmark.

Each scenario mutates one service (path + a route it owns) or a file
outside any service (infra noise), mirroring real PRs:

- seeds the affected scope deterministically (same RNG stream each run)
- ground truth comes from the runtime-aware oracle (full hybrid graph)
- the static baseline predictor only ever sees static import edges
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from impact_engine.models import AnalysisRequest, ChangedFile

from demo.ecosystem import ROUTES, REPO_FILES, SERVICES

INFRA_FILES = ["infra/terraform/main.tf", "k8s/deploy.yaml", "ci/pipeline.yml"]


@dataclass
class Scenario:
    id: int
    changed_services: list[str]
    files: list[ChangedFile]
    truth_services: set[str]
    truth_endpoints: list[tuple[str, str, str]]


def generate_scenarios(
    count: int = 60, seed: int = 42, include_infra_noise: bool = True
) -> list[Scenario]:
    rng = random.Random(seed)
    from demo.ecosystem import build_full_graph, truth_broken_endpoints, truth_set

    graph = build_full_graph()
    scenarios: list[Scenario] = []
    for i in range(count):
        if include_infra_noise and i % 9 == 0:
            path = rng.choice(INFRA_FILES)
            scenarios.append(
                Scenario(
                    id=i,
                    changed_services=[],
                    files=[ChangedFile(path=path, content="changed: true")],
                    truth_services=set(),
                    truth_endpoints=[],
                )
            )
            continue
        svc = rng.choice(SERVICES)
        method, route = rng.choice(ROUTES[svc])
        file_path = f"services/{svc}/app.py"
        mutated = _mutate_route_source(REPO_FILES[file_path], route)
        scenarios.append(
            Scenario(
                id=i,
                changed_services=[svc],
                files=[ChangedFile(path=file_path, content=mutated)],
                truth_services=truth_set(graph, [svc]),
                truth_endpoints=truth_broken_endpoints(graph, [svc]),
            )
        )
    return scenarios


def scenario_request(scenario: Scenario, description: str) -> AnalysisRequest:
    return AnalysisRequest(
        changed_files=scenario.files,
        change_description=description,
        pr_identifier=f"benchmark#{scenario.id}",
    )


def _mutate_route_source(source: str, route: str) -> str:
    """Mutate the changed file: mark the changed file + bump a signature."""
    return source.rstrip() + f"\n# modified route {route}\n"


def default_scenario_descriptions(scenarios: list[Scenario]) -> list[str]:
    return [
        f"refactor checkout flow to add idempotency keys (scenario {s.id})"
        if s.changed_services
        else "update CI pipeline manifests"
        for s in scenarios
    ]