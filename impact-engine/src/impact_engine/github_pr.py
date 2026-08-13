"""GitHub integration: webhook payload parsing + PR diff fetching.

The engine accepts the ``pull_request`` webhook body directly
(``opened`` / ``synchronize`` events) and maps it to an
``AnalysisRequest``. For paths not present in the payload it can fall back
to the REST API (``GITHUB_TOKEN`` + ``GITHUB_REPO`` env) to list changed
files, and optionally fetch file contents for static re-parsing.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from .models import AnalysisRequest, ChangedFile


def parse_webhook(payload: dict[str, Any]) -> AnalysisRequest | None:
    """Build an AnalysisRequest from a GitHub pull_request webhook body."""
    if payload.get("action") not in ("opened", "synchronize", "reopened", "ready_for_review"):
        # skip PRs that weren't updated, dependabot noise, etc.
        return None
    pr = payload.get("pull_request")
    if not pr:
        return None
    repo = payload.get("repository") or {}
    full_name = repo.get("full_name", "")
    head = pr.get("head") or {}
    description = pr.get("title") or "" + (pr.get("body") or "")
    changed_files = [
        ChangedFile(path=f["filename"])
        for f in pr.get("files") or []
    ]
    if not changed_files:
        return None
    return AnalysisRequest(
        changed_files=changed_files,
        change_description=f"{pr.get('title', '')} {pr.get('body', '')}".strip(),
        branch=head.get("ref", ""),
        pr_identifier=f"{full_name}#{pr.get('number')}",
    )


async def fetch_pr_files(
    repo: str,
    pr_number: int,
    token: str | None = None,
    base_url: str = "https://api.github.com",
) -> list[ChangedFile]:
    tok = token or os.environ.get("GITHUB_TOKEN")
    if not tok:
        raise ValueError("GITHUB_TOKEN required to fetch PR diff")
    headers = {
        "Authorization": f"Bearer {tok}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            f"{base_url}/repos/{repo}/pulls/{pr_number}/files?per_page=100",
            headers=headers,
        )
        resp.raise_for_status()
        files = resp.json()
        return [ChangedFile(path=f["filename"]) for f in files]


def pr_identifier(repo: str, number: int) -> str:
    return f"{repo}#{number}"