from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .pipeline import GraphBuildCandidate

_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "as",
        "for",
        "from",
        "in",
        "into",
        "of",
        "on",
        "or",
        "show",
        "the",
        "to",
        "use",
        "with",
    }
)


@dataclass(frozen=True)
class GraphLibraryMatchResult:
    candidates: tuple[GraphBuildCandidate, ...]
    query_terms: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "queryTerms": list(self.query_terms),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }


def match_graph_library_candidates(
    *,
    goal: str,
    node_catalog: dict[str, Any],
    limit: int = 24,
) -> GraphLibraryMatchResult:
    terms = _goal_terms(goal)
    raw_nodes = node_catalog.get("nodes")
    nodes = raw_nodes if isinstance(raw_nodes, list) else []
    candidates: list[GraphBuildCandidate] = []
    for item in nodes:
        if not isinstance(item, dict):
            continue
        candidate = _candidate_from_catalog_item(item, terms=terms)
        if candidate.score <= 0.0:
            continue
        candidates.append(candidate)
    candidates.sort(key=lambda candidate: (-candidate.score, candidate.node_type))
    capped = max(1, min(int(limit), 200))
    return GraphLibraryMatchResult(candidates=tuple(candidates[:capped]), query_terms=terms)


def _candidate_from_catalog_item(item: dict[str, Any], *, terms: tuple[str, ...]) -> GraphBuildCandidate:
    node_type = str(item.get("nodeType") or "").strip()
    kind = str(item.get("kind") or "").strip()
    service_class = str(item.get("serviceClass") or "").strip()
    operator_class = str(item.get("operatorClass") or "").strip()
    label = str(item.get("label") or "").strip()
    description = str(item.get("description") or "").strip()
    search_text = _catalog_search_text(item)
    matched_terms: list[str] = []
    score = 0.0
    for term in terms:
        if not term:
            continue
        if term in search_text:
            matched_terms.append(term)
            score += _term_score(term, item)
    candidate_kind = "service" if kind == "service" else "operator"
    return GraphBuildCandidate(
        kind=candidate_kind,
        node_type=node_type,
        service_class=service_class,
        operator_class=operator_class,
        label=label,
        description=description,
        score=round(score, 3),
        matched_terms=tuple(dict.fromkeys(matched_terms)),
    )


def _catalog_search_text(item: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("nodeType", "label", "kind", "serviceClass", "operatorClass", "description"):
        value = item.get(key)
        if value is not None:
            parts.append(str(value))
    tags = item.get("tags")
    if isinstance(tags, list):
        parts.extend(str(tag) for tag in tags)
    ports = item.get("inputs")
    if isinstance(ports, list):
        for port in ports:
            if isinstance(port, dict):
                parts.append(str(port.get("name") or ""))
                parts.append(str(port.get("kind") or ""))
    ports = item.get("outputs")
    if isinstance(ports, list):
        for port in ports:
            if isinstance(port, dict):
                parts.append(str(port.get("name") or ""))
                parts.append(str(port.get("kind") or ""))
    fields = item.get("stateFields")
    if isinstance(fields, list):
        for field in fields:
            if isinstance(field, dict):
                parts.append(str(field.get("name") or ""))
                parts.append(str(field.get("description") or ""))
    return " ".join(parts).lower()


def _goal_terms(goal: str) -> tuple[str, ...]:
    raw = str(goal or "").lower()
    replacements = {
        "1hz": "1 hz",
        "0-100": "0 100",
        "正弦": "sine",
        "显示": "viz",
        "展示": "viz",
        "可视化": "viz",
    }
    text = raw
    for needle, replacement in replacements.items():
        text = text.replace(needle, replacement)
    terms = [
        token.strip(" ,.;:()[]{}<>\"'")
        for token in text.replace("/", " ").replace("_", " ").split()
    ]
    aliases: list[str] = []
    for term in terms:
        if not term:
            continue
        aliases.append(term)
        if term == "sin":
            aliases.append("sine")
        if term == "plot":
            aliases.append("viz")
        if term == "visualize":
            aliases.append("viz")
    return tuple(dict.fromkeys(alias for alias in aliases if len(alias) >= 2 and alias not in _STOP_WORDS))


def _term_score(term: str, item: dict[str, Any]) -> float:
    label = str(item.get("label") or "").lower()
    node_type = str(item.get("nodeType") or "").lower()
    operator_class = str(item.get("operatorClass") or "").lower()
    service_class = str(item.get("serviceClass") or "").lower()
    if term in node_type or term in operator_class or term in service_class:
        return 2.0
    if term in label:
        return 1.5
    return 1.0
