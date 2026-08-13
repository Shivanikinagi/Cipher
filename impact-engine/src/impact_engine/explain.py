"""AI-generated explanations for impact predictions.

Default: deterministic, per-service templated narratives built from the
predicted risk signals (fully offline, so the API works without keys).
Optional: when ``IMPACT_ENGINE_LLM_URL`` is set (any OpenAI-compatible
chat/completions endpoint) a single natural-language summary is generated
and prepended. The LLM call is best-effort and never blocks the response.
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx

from .models import AffectedService, BrokenEndpoint, IncidentSimilarity

_LEVEL_EMOJI = {"High": "High risk", "Medium": "Medium risk", "Low": "Low risk"}


def build_explanation(
    affected: list[AffectedService],
    broken: list[BrokenEndpoint],
    change_description: str,
    similar_incidents: list[IncidentSimilarity],
    graph_counts: dict[str, int],
) -> str:
    parts: list[str] = []
    if change_description:
        parts.append(f"Change: {change_description}")

    total = len(affected)
    high = sum(1 for a in affected if a.risk_level.value == "High")
    parts.append(
        f"Predicted impact: {total} service(s) affected, {high} of them high risk."
    )

    for a in affected[:8]:
        level = _LEVEL_EMOJI[a.risk_level.value]
        bullet = f"- {a.service} [{level}, score {a.risk_score:.2f}]"
        if a.reasons:
            bullets = "; ".join(a.reasons[:3])
            bullet += f" - {bullets}"
        parts.append(bullet)
    if len(affected) > 8:
        parts.append(f"- ... and {len(affected) - 8} more")

    if broken:
        parts.append("Potentially broken endpoints:")
        for b in broken[:5]:
            e = b.endpoint
            parts.append(
                f"- {e.method} {e.service}{e.route} (asserted by caller; {b.reason})"
            )

    if similar_incidents:
        top = similar_incidents[0]
        parts.append(
            f"Note: similar past incident '{top.summary}' scored "
            f"{top.score:.0%} similarity - consider rollback runbook."
        )

    parts.append(
        "Graph: "
        + ", ".join(f"{k.replace('_', ' ')}={v}" for k, v in sorted(graph_counts.items()))
    )
    return "\n".join(parts)


async def llm_summary(prediction_text: str, change_description: str) -> str | None:
    """Best-effort LLM call to an OpenAI-compatible endpoint."""
    base = os.environ.get("IMPACT_ENGINE_LLM_URL")
    api_key = os.environ.get("IMPACT_ENGINE_LLM_API_KEY")
    model = os.environ.get("IMPACT_ENGINE_LLM_MODEL", "gpt-4o-mini")
    if not base:
        return None
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            payload: dict[str, Any] = {
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a senior SRE explaining predicted change "
                            "impact for microservices. Be concise, signal-first, "
                            "and cite the blast-radius evidence."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Change: {change_description}\n\nPrediction:\n"
                            f"{prediction_text}"
                        ),
                    },
                ],
                "temperature": 0.2,
                "max_tokens": 300,
            }
            resp = await client.post(f"{base}/chat/completions", json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
    except Exception:
        return None


def render_prediction_for_llm(
    affected: list[AffectedService], broken: list[BrokenEndpoint]
) -> str:
    return json.dumps(
        {
            "affected_services": [
                a.model_dump() for a in affected
            ],
            "broken_endpoints": [
                {"endpoint": b.endpoint.model_dump(), "reason": b.reason}
                for b in broken
            ],
        },
        indent=2,
    )