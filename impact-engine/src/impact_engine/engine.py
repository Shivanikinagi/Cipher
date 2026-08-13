"""Analysis orchestration: PR diff -> affected services / broken endpoints.

Pipelines (LangGraph-ready; agents map to these stages):

1. ``parse``   — resolve changed files to owning services + changed routes
2. ``predict`` — hybrid blast-radius + broken-endpoint detection
3. ``score``   — risk scoring with incident similarity
4. ``explain`` — AI narrative
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from . import explain
from .graph import HybridGraph
from .incidents import IncidentStore
from .models import (
    AffectedService,
    AnalysisRequest,
    AnalyzeResult,
    BrokenEndpoint,
    ChangedFile,
    EdgeKind,
    Endpoint,
    IncidentSimilarity,
    RiskLevel,
)
from .risk import (
    aggregate_per_service,
    blast_radius,
    detect_broken_endpoints,
    impact_radius,
)
from .static_parser import StaticAnalysis, parse_repository, resolve_service


@dataclass
class ChangeContext:
    request: AnalysisRequest
    service_map: dict[str, str] = field(default_factory=dict)
    static: StaticAnalysis = field(default_factory=StaticAnalysis)
    changed_services: list[str] = field(default_factory=list)
    changed_routes: set[tuple[str, str, str]] = field(default_factory=set)


class ImpactEngine:
    def __init__(
        self,
        graph: HybridGraph,
        incidents: IncidentStore,
        service_map: dict[str, str] | None = None,
        llm_enabled: bool = True,
    ) -> None:
        self.graph = graph
        self.incidents = incidents
        self.service_map = service_map or {}
        self.llm_enabled = llm_enabled

    # ------------------------------------------------------------------
    # stage 1: parse
    # ------------------------------------------------------------------
    def parse(self, request: AnalysisRequest) -> ChangeContext:
        ctx = ChangeContext(request=request, service_map=self.service_map)
        files = {f.path: f.content or "" for f in request.changed_files if f.content is not None}

        # defined routes from changed Python files
        for cf in request.changed_files:
            if not cf.content:
                continue
            suffix = cf.path.rsplit(".", 1)[-1].lower()
            if suffix != "py":
                continue
            svc = resolve_service(cf.path, self.service_map)
            if not svc:
                continue
            for kind, name in parse_repository({cf.path: cf.content}).definition_points.get(
                cf.path, []
            ):
                if kind == "route":
                    ctx.changed_routes.add((svc, "GET", name if name.startswith("/") else "/" + name))

        ctx.static = parse_repository(files, self.service_map) if files else StaticAnalysis()
        ctx.changed_services = sorted(
            {
                svc
                for cf in request.changed_files
                if (svc := resolve_service(cf.path, self.service_map))
            }
        )
        ctx.changed_services = [s for s in ctx.changed_services if s not in request.exclude_services]
        return ctx

    # ------------------------------------------------------------------
    # stage 2: predict
    # ------------------------------------------------------------------
    def predict(self, ctx: ChangeContext) -> list[dict]:
        # static edges discovered from the PR itself (new imports)
        static_added: list[tuple[str, str, str]] = [
            (src, dst, EdgeKind.STATIC_IMPORT.value) for src, dst in ctx.static.edges
        ]
        seeds = [s for s in ctx.changed_services if s in self.graph.services()]
        blasts = impact_radius(self.graph, seeds)
        broken = detect_broken_endpoints(self.graph, ctx.changed_routes)
        return {
            "seeds": seeds,
            "blasts": blasts,
            "broken": broken,
            "static_added": static_added,
        }

    # ------------------------------------------------------------------
    # stage 3: score
    # ------------------------------------------------------------------
    def score(self, ctx: ChangeContext, predicted: dict) -> list[AffectedService]:
        incident_factor = 0.0
        if ctx.request.change_description:
            incident_factor = self.incidents.incident_risk_factor(
                ctx.request.change_description
            )
        aggregated = aggregate_per_service(
            self.graph, predicted["blasts"], predicted["broken"], incident_factor
        )
        services = []
        for a in aggregated:
            if a["service"] in ctx.request.exclude_services:
                continue
            services.append(AffectedService(**a))
        services.sort(key=lambda a: a.risk_score, reverse=True)
        return services

    # ------------------------------------------------------------------
    # stage 4: explain
    # ------------------------------------------------------------------
    async def explain(
        self,
        ctx: ChangeContext,
        affected: list[AffectedService],
        broken: list[BrokenEndpoint],
        similarities: list[IncidentSimilarity],
    ) -> str:
        text = explain.build_explanation(
            affected=affected,
            broken=broken,
            change_description=ctx.request.change_description,
            similar_incidents=similarities,
            graph_counts=self.graph.edge_counts(),
        )
        if self.llm_enabled:
            llm = await explain.llm_summary(
                explain.render_prediction_for_llm(affected, broken),
                ctx.request.change_description,
            )
            if llm:
                text = f"{llm}\n\n{text}"
        return text

    # ------------------------------------------------------------------
    # full pipeline
    # ------------------------------------------------------------------
    async def analyze(self, request: AnalysisRequest) -> AnalyzeResult:
        t0 = time.perf_counter()
        ctx = self.parse(request)
        predicted = self.predict(ctx)

        similarities = (
            self.incidents.similar(
                request.change_description, ctx.changed_services, limit=5
            )
            if request.change_description
            else []
        )

        broken_models: list[BrokenEndpoint] = []
        for b in predicted["broken"]:
            owner_level = RiskLevel.HIGH if b["traffic_volume"] > 0.4 else RiskLevel.MEDIUM
            broken_models.append(
                BrokenEndpoint(
                    endpoint=Endpoint(
                        service=b["caller"],
                        method=b["caller_method"],
                        route=b["caller_route"],
                    ),
                    reason=(
                        f"calls changed endpoint on {b['callee']}; "
                        f"edge carries {b['traffic_volume'] * 100:.0f}% traffic"
                    ),
                    risk_level=owner_level,
                )
            )

        affected = self.score(ctx, predicted)
        explanation = await self.explain(ctx, affected, broken_models, similarities)
        return AnalyzeResult(
            affected_services=affected,
            broken_endpoints=broken_models,
            explanation=explanation,
            incident_similarities=similarities,
        )