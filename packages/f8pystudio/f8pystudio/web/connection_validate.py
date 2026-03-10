from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from f8pysdk import F8OperatorSpec, F8ServiceSpec
from f8pysdk.generated import F8StateAccess, F8StateSpec
from f8pysdk.msgspec_codec import copy_model, validate_as

from ..constants import SERVICE_CLASS as STUDIO_SERVICE_CLASS, STUDIO_SERVICE_ID
from ..nodegraph.edge_rules import EdgeRuleNodeInfo, validate_connection_by_infos
from .graph_doc import GraphDoc, GraphNode
from .json_sanitize import sanitize_legacy_nulls


@dataclass(frozen=True)
class ConnectionEndpoint:
    nodeId: str
    port: str


def _encode_out_port(kind: str, raw: str) -> str:
    base = str(raw or "").strip()
    if kind == "exec":
        return f"{base}[E]"
    if kind == "data":
        return f"{base}[D]"
    if kind == "state":
        return f"{base}[S]"
    return base


def _encode_in_port(kind: str, raw: str) -> str:
    base = str(raw or "").strip()
    if kind == "exec":
        return f"[E]{base}"
    if kind == "data":
        return f"[D]{base}"
    if kind == "state":
        return f"[S]{base}"
    return base


def _coerce_spec(spec_json: dict[str, Any]) -> F8OperatorSpec | F8ServiceSpec:
    cleaned = sanitize_legacy_nulls(spec_json)
    if not isinstance(cleaned, dict):
        cleaned = dict(spec_json)
    if "operatorClass" in cleaned:
        return validate_as(F8OperatorSpec, cleaned)
    return validate_as(F8ServiceSpec, cleaned)


def _effective_state_fields(spec: F8OperatorSpec | F8ServiceSpec, ui_overrides: dict[str, Any]) -> list[F8StateSpec]:
    fields = list(spec.stateFields or [])
    if not fields:
        return fields
    state_over = ui_overrides.get("stateFields")
    if not isinstance(state_over, dict) or not state_over:
        return fields
    allowed_keys = {"showOnNode", "uiControl", "uiLanguage", "label", "description"}
    out: list[F8StateSpec] = []
    for f in fields:
        name = str(f.name or "").strip()
        ov = state_over.get(name) if name else None
        if not isinstance(ov, dict) or not ov:
            out.append(f)
            continue
        patch = {k: ov.get(k) for k in allowed_keys if k in ov}
        try:
            out.append(copy_model(f, update=patch))
        except (TypeError, ValueError):
            out.append(f)
    return out


def _state_access_value(access: object) -> str:
    if isinstance(access, F8StateAccess):
        return str(access.value)
    if isinstance(access, str):
        return access
    try:
        value = access.value  # type: ignore[attr-defined]
    except AttributeError:
        return str(access)
    return str(value)


def _service_id_for_operator_node(node: GraphNode, spec: F8OperatorSpec) -> str | None:
    service_class = str(spec.serviceClass or "").strip()
    if service_class == STUDIO_SERVICE_CLASS:
        return STUDIO_SERVICE_ID
    svc_id = str((node.state or {}).get("svcId") or "").strip()
    if not svc_id:
        svc_id = str((node.custom or {}).get("svcId") or "").strip()
    return svc_id or None


def _node_info(node: GraphNode, spec: F8OperatorSpec | F8ServiceSpec) -> EdgeRuleNodeInfo | None:
    node_id = str(node.id or "").strip()
    if not node_id:
        return None
    if isinstance(spec, F8ServiceSpec):
        return EdgeRuleNodeInfo(node_id=node_id, service_id=node_id, is_operator=False)
    svc_id = _service_id_for_operator_node(node, spec)
    if not svc_id:
        return None
    return EdgeRuleNodeInfo(node_id=node_id, service_id=svc_id, is_operator=True)


def validate_connection(
    doc: GraphDoc,
    *,
    kind: str,
    from_ep: ConnectionEndpoint,
    to_ep: ConnectionEndpoint,
) -> tuple[bool, str]:
    kind_norm = str(kind or "").strip().lower()
    if kind_norm not in {"exec", "data", "state"}:
        return False, f"invalid kind: {kind!r}"
    if not from_ep.nodeId or not to_ep.nodeId or not from_ep.port or not to_ep.port:
        return False, "missing endpoint fields"

    nodes_by_id: dict[str, GraphNode] = {str(n.id): n for n in list(doc.nodes or [])}
    src = nodes_by_id.get(from_ep.nodeId)
    dst = nodes_by_id.get(to_ep.nodeId)
    if src is None or dst is None:
        return False, "missing node"

    if not isinstance(src.spec, dict) or not isinstance(dst.spec, dict):
        return False, "invalid spec"
    try:
        src_spec = _coerce_spec(dict(src.spec))
        dst_spec = _coerce_spec(dict(dst.spec))
    except Exception as exc:
        return False, f"invalid spec: {type(exc).__name__}: {exc}"

    # Port existence + direction.
    if kind_norm == "exec":
        if not isinstance(src_spec, F8OperatorSpec) or not isinstance(dst_spec, F8OperatorSpec):
            return False, "exec edges require operator->operator endpoints"
        src_out = {str(x) for x in list(src_spec.execOutPorts or [])}
        dst_in = {str(x) for x in list(dst_spec.execInPorts or [])}
        if str(from_ep.port) not in src_out:
            return False, "unknown src execOut port"
        if str(to_ep.port) not in dst_in:
            return False, "unknown dst execIn port"

    elif kind_norm == "data":
        src_out: set[str] = set()
        for p in list(src_spec.dataOutPorts or []):
            try:
                name = str(p.name or "").strip()
            except (AttributeError, TypeError):
                name = ""
            if name:
                src_out.add(name)
        dst_in: set[str] = set()
        for p in list(dst_spec.dataInPorts or []):
            try:
                name = str(p.name or "").strip()
            except (AttributeError, TypeError):
                name = ""
            if name:
                dst_in.add(name)
        if str(from_ep.port) not in src_out:
            return False, "unknown src dataOut port"
        if str(to_ep.port) not in dst_in:
            return False, "unknown dst dataIn port"

    else:  # state
        src_fields = _effective_state_fields(src_spec, dict(src.uiOverrides or {}))
        dst_fields = _effective_state_fields(dst_spec, dict(dst.uiOverrides or {}))

        src_ok = False
        for f in src_fields:
            name = str(f.name or "").strip()
            if name != str(from_ep.port):
                continue
            show = bool(f.showOnNode)
            if not show:
                return False, "src state field not shown on node"
            access = _state_access_value(f.access)
            if access in ("rw", "ro"):
                src_ok = True
            break
        if not src_ok:
            return False, "src state field not readable"

        dst_ok = False
        for f in dst_fields:
            name = str(f.name or "").strip()
            if name != str(to_ep.port):
                continue
            show = bool(f.showOnNode)
            if not show:
                return False, "dst state field not shown on node"
            access = _state_access_value(f.access)
            if access in ("rw", "wo"):
                dst_ok = True
            break
        if not dst_ok:
            return False, "dst state field not writable"

    # Studio policy (exec cross-service) and same-kind enforcement.
    allowed, reason = validate_connection_by_infos(
        out_port_name=_encode_out_port(kind_norm, from_ep.port),
        in_port_name=_encode_in_port(kind_norm, to_ep.port),
        out_info=_node_info(src, src_spec),
        in_info=_node_info(dst, dst_spec),
    )
    return bool(allowed), str(reason or "")
