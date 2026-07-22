from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from f8pysdk.specs import F8DataPortSpec

from f8pystudio.assets.components.component_compatibility import (
    SemanticSignal,
    evaluate_component_compatibility,
)
from f8pystudio.assets.components.component_models import F8ComponentEntry
from f8pystudio.assets.components.component_taxonomy import component_taxonomy_from_tags

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
    components: tuple[ComponentLibraryCandidate, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "queryTerms": list(self.query_terms),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "components": [component.to_dict() for component in self.components],
        }


@dataclass(frozen=True)
class ComponentCompatibilityEvidence:
    evaluated: bool
    compatible: bool | None
    signal: str | None
    source_port: str | None
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluated": self.evaluated,
            "compatible": self.compatible,
            "signal": self.signal,
            "sourcePort": self.source_port,
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class ComponentLibraryCandidate:
    component_id: str
    name: str
    description: str
    source: str
    installed: bool
    score: float
    matched_terms: tuple[str, ...]
    role: str | None
    workflows: tuple[str, ...]
    signals: tuple[str, ...]
    protocols: tuple[str, ...]
    compatibility: ComponentCompatibilityEvidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "component",
            "componentId": self.component_id,
            "name": self.name,
            "description": self.description,
            "source": self.source,
            "installed": self.installed,
            "score": self.score,
            "matchedTerms": list(self.matched_terms),
            "role": self.role,
            "workflows": list(self.workflows),
            "signals": list(self.signals),
            "protocols": list(self.protocols),
            "compatibility": self.compatibility.to_dict(),
        }


def match_graph_library_candidates(
    *,
    goal: str,
    node_catalog: dict[str, Any],
    component_entries: Iterable[F8ComponentEntry] = (),
    source_port: F8DataPortSpec | None = None,
    signal: SemanticSignal | None = None,
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
    component_candidates = _match_component_candidates(
        entries=component_entries,
        terms=terms,
        source_port=source_port,
        signal=signal,
    )
    return GraphLibraryMatchResult(
        candidates=tuple(candidates[:capped]),
        query_terms=terms,
        components=tuple(component_candidates[:capped]),
    )


def _match_component_candidates(
    *,
    entries: Iterable[F8ComponentEntry],
    terms: tuple[str, ...],
    source_port: F8DataPortSpec | None,
    signal: SemanticSignal | None,
) -> list[ComponentLibraryCandidate]:
    entries_by_id: dict[str, F8ComponentEntry] = {}
    for entry in entries:
        component_id = str(entry.record.componentId or "").strip()
        if component_id:
            entries_by_id.setdefault(component_id, entry)

    candidates: list[ComponentLibraryCandidate] = []
    for entry in entries_by_id.values():
        candidate = _component_candidate_from_entry(
            entry,
            terms=terms,
            source_port=source_port,
            signal=signal,
        )
        if candidate.score > 0.0:
            candidates.append(candidate)
    candidates.sort(key=lambda candidate: (-candidate.score, candidate.name.lower(), candidate.component_id))
    return candidates


def _component_candidate_from_entry(
    entry: F8ComponentEntry,
    *,
    terms: tuple[str, ...],
    source_port: F8DataPortSpec | None,
    signal: SemanticSignal | None,
) -> ComponentLibraryCandidate:
    record = entry.record
    tags = list(record.tags or [])
    taxonomy = component_taxonomy_from_tags(tags)
    search_text = " ".join(
        [
            str(record.componentId or ""),
            str(record.name or ""),
            str(record.description or ""),
            *tags,
        ]
    ).lower()
    matched_terms = tuple(term for term in terms if term and term in search_text)
    score = sum(_component_term_score(term=term, entry=entry) for term in matched_terms)
    return ComponentLibraryCandidate(
        component_id=str(record.componentId or "").strip(),
        name=str(record.name or "").strip(),
        description=str(record.description or "").strip(),
        source=entry.source.value,
        installed=bool(entry.installed),
        score=round(score, 3),
        matched_terms=matched_terms,
        role=None if taxonomy.role is None else taxonomy.role.value,
        workflows=tuple(sorted(taxonomy.workflows)),
        signals=tuple(sorted(taxonomy.signals)),
        protocols=tuple(sorted(taxonomy.protocols)),
        compatibility=_component_compatibility_evidence(
            source_port=source_port,
            signal=signal,
            component_tags=tags,
        ),
    )


def _component_term_score(*, term: str, entry: F8ComponentEntry) -> float:
    record = entry.record
    identity_text = f"{record.componentId} {record.name}".lower()
    taxonomy_text = " ".join(str(tag) for tag in list(record.tags or [])).lower()
    if term in identity_text:
        return 2.0
    if term in taxonomy_text:
        return 1.5
    return 1.0


def _component_compatibility_evidence(
    *,
    source_port: F8DataPortSpec | None,
    signal: SemanticSignal | None,
    component_tags: Iterable[str],
) -> ComponentCompatibilityEvidence:
    if source_port is None or signal is None:
        return ComponentCompatibilityEvidence(
            evaluated=False,
            compatible=None,
            signal=None if signal is None else signal.value,
            source_port=None if source_port is None else source_port.name,
        )
    decision = evaluate_component_compatibility(
        source_port=source_port,
        signal=signal,
        component_tags=component_tags,
    )
    return ComponentCompatibilityEvidence(
        evaluated=True,
        compatible=decision.compatible,
        signal=signal.value,
        source_port=source_port.name,
        reasons=decision.reasons,
        warnings=decision.warnings,
    )


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
    terms = [token.strip(" ,.;:()[]{}<>\"'") for token in text.replace("/", " ").replace("_", " ").split()]
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
