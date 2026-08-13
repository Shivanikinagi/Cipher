"""Historical incident store with embedding-free similarity matching.

For the MVP the store is a JSON file (embedded store ships with the package).
Similarity uses token Jaccard + bigram overlap between the change
description and incident text; optionally extended with an embedding
provider (ChromaDB) at the adapter boundary. All scores in [0, 1].
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from .models import Incident, IncidentSimilarity

_WORD_RE = re.compile(r"[a-z0-9_]+")


def _tokens(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def token_similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    if inter == 0:
        return 0.0
    jaccard = inter / union
    # bigram proximity bonus for shared phrases
    ba, bb = _bigrams(a), _bigrams(b)
    overlap = len(ba & bb)
    return min(1.0, jaccard + 0.25 * (overlap / max(len(bb), 1)))


def _bigrams(text: str) -> set[str]:
    words = _WORD_RE.findall(text.lower())
    return {f"{words[i]} {words[i + 1]}" for i in range(len(words) - 1)}


class IncidentStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None
        self._incidents: list[Incident] = []
        if self.path and self.path.exists():
            self.load()

    # ------------------------------------------------------------------
    # persistence
    # ------------------------------------------------------------------
    def load(self) -> None:
        data: list[dict[str, Any]] = json.loads(self.path.read_text(encoding="utf-8"))
        self._incidents = [Incident(**d) for d in data]

    def save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(
                    [i.model_dump() for i in self._incidents],
                    indent=2,
                ),
                encoding="utf-8",
            )

    def add(self, incident: Incident) -> Incident:
        incident.id = incident.id or f"INC-{len(self._incidents) + 1:04d}"
        existing = [i for i in self._incidents if i.id == incident.id]
        if existing:
            self._incidents.remove(existing[0])
        self._incidents.append(incident)
        self.save()
        return incident

    def all(self) -> list[Incident]:
        return list(self._incidents)

    def clear(self) -> None:
        self._incidents = []
        if self.path:
            self.save()

    # ------------------------------------------------------------------
    # similarity search
    # ------------------------------------------------------------------
    def similar(
        self,
        change_description: str,
        affected_services: list[str],
        limit: int = 5,
        min_score: float = 0.0,
    ) -> list[IncidentSimilarity]:
        candidates: list[IncidentSimilarity] = []
        for inc in self._incidents:
            service_overlap = len(set(inc.affected_services) & set(affected_services))
            svc_bonus = 0.2 * min(1.0, service_overlap)
            text_score = token_similarity(change_description, inc.text or inc.summary)
            score = math.ceil(min(1.0, text_score * 0.8 + svc_bonus) * 100) / 100
            if score >= min_score:
                candidates.append(
                    IncidentSimilarity(
                        incident_id=inc.id or "",
                        score=score,
                        summary=inc.summary,
                    )
                )
        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates[:limit]

    def incident_risk_factor(self, change_description: str) -> float:
        """Peak similarity signal for risk scoring."""
        hits = self.similar(change_description, [], limit=3, min_score=0.15)
        if not hits:
            return 0.0
        return max(h.score for h in hits)