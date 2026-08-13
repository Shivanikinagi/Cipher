"""Static dependency analysis across languages.

Primary: Python via ``ast`` (accurate, full language support).
Fallback: regex-based import extractors for TypeScript/JavaScript/Java
(deployed services commonly mix these; the demo benchmark uses Python).

Output is a set of (src_service, dst_service) edges plus the endpoints
(callables / HTTP routes) each file defines so runtime telemetry can be
joined to it.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

ServiceResolver = Callable[[str], str]

# ---------------------------------------------------------------------------
# import extractors
# ---------------------------------------------------------------------------

_TS_IMPORT_RE = re.compile(
    r"(?:import\s+(?:type\s+)?[\w*{},\s]+from\s+['\"]([^'\"]+)['\"]|require\(\s*['\"]([^'\"]+)['\"]\s*\))"
)
_JAVA_IMPORT_RE = re.compile(r"^\s*import\s+([\w.]+);", re.MULTILINE)


def _python_imports(source: str) -> list[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    mods: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mods.append(node.module)
    return mods


def _ts_imports(source: str) -> list[str]:
    return [m or n for m, n in _TS_IMPORT_RE.findall(source) if (m or n)]


def _java_imports(source: str) -> list[str]:
    return _JAVA_IMPORT_RE.findall(source)


def _python_definition_points(source: str) -> list[tuple[str, str]]:
    """(kind, name) pairs: 'function'/'class' definitions and Flask/FastAPI routes.

    Routes are extracted with a regex (tolerant of truncated/invalid source,
    e.g. a decorator whose body is missing in a partial diff); function/class
    detection uses ``ast`` and degrades gracefully on SyntaxError.
    """
    points: list[tuple[str, str]] = []
    for m in re.finditer(
        r"@(?:app|route|router)\.(?:get|post|put|patch|delete|route)\s*\(\s*['\"]([^'\"]+)['\"]\s*\)",
        source,
    ):
        points.append(("route", m.group(1)))
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return points
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            points.append(("function", node.name))
        elif isinstance(node, ast.ClassDef):
            points.append(("class", node.name))
    return points


# ---------------------------------------------------------------------------
# file -> service mapping
# ---------------------------------------------------------------------------


def resolve_service(
    file_path: str,
    service_map: dict[str, str],
    default_resolver: ServiceResolver | None = None,
) -> str | None:
    """Map a file to its owning microservice.

    Checks exact path, longest directory prefix, then falls back to the
    top-level directory (skill: ``services/order/api.py`` -> ``services/order``).
    """
    path = file_path.replace("\\", "/").lstrip("/")
    if path in service_map:
        return service_map[path]
    best: tuple[int, str] | None = None
    for prefix, svc in service_map.items():
        p = prefix.replace("\\", "/").rstrip("/")
        if path.startswith(p + "/"):
            depth = p.count("/")
            if best is None or depth > best[0]:
                best = (depth, svc)
    if best:
        return best[1]
    if default_resolver:
        resolved = default_resolver(path)
        if resolved:
            return resolved
    parts = path.split("/")
    return parts[0] if parts and parts[0] else None


# ---------------------------------------------------------------------------
# repository analysis
# ---------------------------------------------------------------------------


@dataclass
class StaticAnalysis:
    edges: set[tuple[str, str]] = field(default_factory=set)
    """(src_service, dst_service) import edges."""

    dependencies_per_file: dict[str, list[str]] = field(default_factory=dict)
    """file path -> raw imported module names."""

    definition_points: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    """file path -> [(kind, name)] definitions/routes."""


def parse_repository(
    files: dict[str, str],
    service_map: dict[str, str] | None = None,
    default_resolver: ServiceResolver | None = None,
) -> StaticAnalysis:
    """Analyze a repository snapshot (path -> source text)."""
    service_map = service_map or {}
    result = StaticAnalysis()

    known_services: set[str] = set(service_map.values())
    for path, source in files.items():
        svc = resolve_service(path, service_map, default_resolver)
        if not svc:
            continue
        suffix = Path(path).suffix.lower()
        if suffix == ".py":
            imported = _python_imports(source)
            result.definition_points[path] = _python_definition_points(source)
        elif suffix in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
            imported = _ts_imports(source)
        elif suffix == ".java":
            imported = _java_imports(source)
        else:
            continue
        result.dependencies_per_file[path] = imported
        for mod in imported:
            dep_svc = _module_to_service(
                mod, path, service_map, default_resolver, known_services
            )
            if dep_svc and dep_svc != svc:
                result.edges.add((svc, dep_svc))

    return result


def _module_to_service(
    module: str,
    from_path: str,
    service_map: dict[str, str],
    default_resolver: ServiceResolver | None,
    known_services: set[str],
) -> str | None:
    """Map an imported module name to an owning service.

    Strategy:
    1. relative imports stay within the same service -> skip
    2. module head matches a known service by normalized name
       (``payment_service`` -> ``payment-service``)
    3. fall back to a directory probe against the service map
    """
    if module.startswith("."):
        return None
    head = module.split(".")[0]
    normalized = re.sub(r"[^a-z0-9]", "", head.lower())
    for svc in known_services:
        if normalized and normalized == re.sub(r"[^a-z0-9]", "", svc.lower()):
            return svc
    probe = head + "/__init__.py"
    probed = resolve_service(probe, service_map, default_resolver)
    # the top-level-dir fallback would fabricate services for unknown
    # modules; only trust a probe that resolved through the map/resolver
    return probed if probed and probed != head else None