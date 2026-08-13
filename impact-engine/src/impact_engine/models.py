from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def _to_camel(s: str) -> str:
    head, *rest = s.split("_")
    return head + "".join(part.capitalize() for part in rest)


class RiskLevel(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


class EdgeKind(str, Enum):
    STATIC_IMPORT = "static_import"
    RUNTIME_CALL = "runtime_call"
    MESSAGE_QUEUE = "message_queue"


class ChangedFile(BaseModel):
    path: str
    content: str | None = None
    old_content: str | None = None


class Endpoint(BaseModel):
    service: str
    method: str
    route: str

    @property
    def key(self) -> str:
        return f"{self.method} {self.service}{self.route}"


class AffectedService(BaseModel):
    service: str
    risk_level: RiskLevel
    risk_score: float = Field(ge=0.0, le=1.0)
    blast_score: float = Field(ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)


class BrokenEndpoint(BaseModel):
    endpoint: Endpoint
    reason: str
    risk_level: RiskLevel


class Prediction(BaseModel):
    affected_services: list[AffectedService] = Field(default_factory=list)
    broken_endpoints: list[BrokenEndpoint] = Field(default_factory=list)
    explanation: str = ""


class AnalysisRequest(BaseModel):
    changed_files: list[ChangedFile]
    change_description: str = ""
    branch: str = ""
    pr_identifier: str = ""
    exclude_services: list[str] = Field(default_factory=list)


class AnalyzeResult(BaseModel):
    affected_services: list[AffectedService]
    broken_endpoints: list[BrokenEndpoint]
    explanation: str
    incident_similarities: list[IncidentSimilarity] = Field(default_factory=list)


class AnalysisResponse(BaseModel):
    pr_identifier: str
    prediction: AnalyzeResult
    latency_ms: float
    engine_version: str = "impact-engine-0.1.0"


class TraceSpan(BaseModel):
    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    service_name: str = ""
    name: str = ""
    kind: str = "SPAN_KIND_SERVER"
    status: str = "STATUS_CODE_OK"
    attributes: dict[str, Any] = Field(default_factory=dict)
    start_time_unix_nano: int = 0
    end_time_unix_nano: int = 0

    @property
    def is_error(self) -> bool:
        return self.status in ("STATUS_CODE_ERROR", "ERROR")

    @property
    def duration_ms(self) -> float:
        return (self.end_time_unix_nano - self.start_time_unix_nano) / 1e6


class TraceBatch(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=_to_camel)

    resource_spans: list[dict[str, Any]] = Field(default_factory=list)


class MetricPoint(BaseModel):
    service_name: str = ""
    name: str = ""
    value: float = 0.0
    attributes: dict[str, Any] = Field(default_factory=dict)
    timestamp_unix_nano: int = 0


class MetricBatch(BaseModel):
    metrics: list[MetricPoint] = Field(default_factory=list)


class Incident(BaseModel):
    id: str | None = None
    date: str = ""
    summary: str
    affected_services: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    text: str = ""


class IncidentSimilarity(BaseModel):
    incident_id: str
    score: float = Field(ge=0.0, le=1.0)
    summary: str = ""