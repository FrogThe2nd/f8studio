from __future__ import annotations

from typing import Any

from .pipeline import (
    GraphBuildCandidate,
    GraphBuildPlan,
    GraphBuildRequirement,
    GraphConnectionPlan,
    GraphNodePlan,
    GraphValidationTarget,
)


def decode_graph_build_plan(payload: Any) -> GraphBuildPlan:
    if not isinstance(payload, dict):
        raise ValueError("graph build plan must be a JSON object")
    requirement = _decode_requirement(payload.get("requirement"))
    nodes = tuple(_decode_node_plan(item, index=index) for index, item in enumerate(_required_list(payload, "nodes")))
    connections = tuple(
        _decode_connection_plan(item, index=index) for index, item in enumerate(_optional_list(payload, "connections"))
    )
    validation_targets = tuple(
        _decode_validation_target(item, index=index)
        for index, item in enumerate(_optional_list(payload, "validationTargets"))
    )
    candidates = tuple(
        _decode_candidate(item, index=index) for index, item in enumerate(_optional_list(payload, "candidates"))
    )
    return GraphBuildPlan(
        status="planned",
        requirement=requirement,
        summary=str(payload.get("summary") or "Agent graph build plan").strip() or "Agent graph build plan",
        candidates=candidates,
        nodes=nodes,
        connections=connections,
        validation_targets=validation_targets,
    )


def _decode_requirement(payload: Any) -> GraphBuildRequirement:
    data = payload if isinstance(payload, dict) else {}
    return GraphBuildRequirement(
        goal=str(data.get("goal") or "").strip(),
        service_hints=tuple(_text_items(data.get("serviceHints"))),
        operator_hints=tuple(_text_items(data.get("operatorHints"))),
        data_flow_hints=tuple(_text_items(data.get("dataFlowHints"))),
        validation_hints=tuple(_text_items(data.get("validationHints"))),
        visualization_hints=tuple(_text_items(data.get("visualizationHints"))),
    )


def _decode_node_plan(payload: Any, *, index: int) -> GraphNodePlan:
    if not isinstance(payload, dict):
        raise ValueError(f"graph build node #{index} must be a JSON object")
    state_values = payload.get("stateValues")
    state_pairs: list[tuple[str, Any]] = []
    if isinstance(state_values, dict):
        for key, value in state_values.items():
            field = str(key or "").strip()
            if field:
                state_pairs.append((field, value))
    return GraphNodePlan(
        node_type=_required_text(payload, "nodeType", label=f"node #{index}"),
        node_id=_required_text(payload, "nodeId", label=f"node #{index}"),
        name=str(payload.get("name") or "").strip(),
        role=str(payload.get("role") or "").strip(),
        state_values=tuple(state_pairs),
        position=_tuple2(payload.get("position"), label=f"node #{index} position", default=(0.0, 0.0)),
    )


def _decode_connection_plan(payload: Any, *, index: int) -> GraphConnectionPlan:
    if not isinstance(payload, dict):
        raise ValueError(f"graph build connection #{index} must be a JSON object")
    return GraphConnectionPlan(
        from_node_id=_required_text(payload, "fromNodeId", label=f"connection #{index}"),
        from_port=_required_text(payload, "fromPort", label=f"connection #{index}"),
        to_node_id=_required_text(payload, "toNodeId", label=f"connection #{index}"),
        to_port=_required_text(payload, "toPort", label=f"connection #{index}"),
        reason=str(payload.get("reason") or "").strip(),
    )


def _decode_validation_target(payload: Any, *, index: int) -> GraphValidationTarget:
    if not isinstance(payload, dict):
        raise ValueError(f"graph build validation target #{index} must be a JSON object")
    return GraphValidationTarget(
        service_id=_required_text(payload, "serviceId", label=f"validation target #{index}"),
        node_id=_required_text(payload, "nodeId", label=f"validation target #{index}"),
        port=_required_text(payload, "port", label=f"validation target #{index}"),
        description=str(payload.get("description") or "").strip(),
        expected_min=_optional_float(payload.get("expectedMin")),
        expected_max=_optional_float(payload.get("expectedMax")),
    )


def _decode_candidate(payload: Any, *, index: int) -> GraphBuildCandidate:
    if not isinstance(payload, dict):
        raise ValueError(f"graph build candidate #{index} must be a JSON object")
    kind_text = str(payload.get("kind") or "operator").strip()
    kind = "service" if kind_text == "service" else "operator"
    return GraphBuildCandidate(
        kind=kind,
        node_type=str(payload.get("nodeType") or "").strip(),
        service_class=str(payload.get("serviceClass") or "").strip(),
        operator_class=str(payload.get("operatorClass") or "").strip(),
        label=str(payload.get("label") or "").strip(),
        description=str(payload.get("description") or "").strip(),
        score=float(payload.get("score") or 0.0),
        matched_terms=tuple(_text_items(payload.get("matchedTerms"))),
    )


def _required_list(payload: dict[str, Any], key: str) -> list[Any]:
    value = payload.get(key)
    if not isinstance(value, list) or not value:
        raise ValueError(f"graph build plan requires non-empty {key}")
    return list(value)


def _optional_list(payload: dict[str, Any], key: str) -> list[Any]:
    value = payload.get(key)
    if isinstance(value, list):
        return list(value)
    return []


def _required_text(payload: dict[str, Any], key: str, *, label: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ValueError(f"{label} requires {key}")
    return value


def _text_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            out.append(text)
    return out


def _tuple2(value: Any, *, label: str, default: tuple[float, float]) -> tuple[float, float]:
    if value is None:
        return default
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{label} must be a two-item array")
    try:
        return (float(value[0]), float(value[1]))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must contain numbers") from exc


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
