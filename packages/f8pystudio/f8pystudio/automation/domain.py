from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

JsonObject = dict[str, Any]

GraphPatchOpKind = Literal[
    "createNode",
    "deleteNode",
    "connectPorts",
    "disconnectPorts",
    "setNodeState",
    "setNodeName",
    "setNodePorts",
    "setNodeStateFields",
    "moveNode",
    "setUiOverride",
]


@dataclass(frozen=True)
class AutomationError:
    code: str
    message: str
    details: JsonObject = field(default_factory=dict)
    traceback_id: str = ""

    def to_dict(self) -> JsonObject:
        return asdict(self)


@dataclass(frozen=True)
class AutomationResult:
    ok: bool
    result: JsonObject = field(default_factory=dict)
    error: AutomationError | None = None

    def to_dict(self) -> JsonObject:
        return {
            "ok": bool(self.ok),
            "result": dict(self.result),
            "error": None if self.error is None else self.error.to_dict(),
        }


@dataclass(frozen=True)
class GraphPortSnapshot:
    name: str
    kind: str
    direction: str


@dataclass(frozen=True)
class GraphStateFieldSnapshot:
    name: str
    access: str
    label: str = ""
    description: str = ""


@dataclass(frozen=True)
class GraphNodeSnapshot:
    node_id: str
    node_type: str
    name: str
    kind: str
    service_class: str = ""
    operator_class: str = ""
    pos: tuple[float, float] = (0.0, 0.0)
    selected: bool = False
    inputs: tuple[GraphPortSnapshot, ...] = ()
    outputs: tuple[GraphPortSnapshot, ...] = ()
    state_fields: tuple[GraphStateFieldSnapshot, ...] = ()


@dataclass(frozen=True)
class GraphEdgeSnapshot:
    edge_kind: str
    from_node_id: str
    from_port: str
    to_node_id: str
    to_port: str


@dataclass(frozen=True)
class GraphSnapshot:
    revision: int
    node_count: int
    edge_count: int
    selected_node_ids: tuple[str, ...] = ()
    nodes: tuple[GraphNodeSnapshot, ...] = ()
    edges: tuple[GraphEdgeSnapshot, ...] = ()

    def to_dict(self) -> JsonObject:
        return asdict(self)


@dataclass(frozen=True)
class CreateNodeOp:
    node_type: str
    node_id: str = ""
    name: str = ""
    pos: tuple[float, float] = (0.0, 0.0)
    selected: bool = False


@dataclass(frozen=True)
class DeleteNodeOp:
    node_id: str


@dataclass(frozen=True)
class ConnectPortsOp:
    from_node_id: str
    from_port: str
    to_node_id: str
    to_port: str


@dataclass(frozen=True)
class DisconnectPortsOp:
    from_node_id: str
    from_port: str
    to_node_id: str
    to_port: str


@dataclass(frozen=True)
class SetNodeStateOp:
    node_id: str
    field: str
    value: Any


@dataclass(frozen=True)
class SetNodeNameOp:
    node_id: str
    name: str


@dataclass(frozen=True)
class SetNodePortsOp:
    node_id: str
    data_in_ports: tuple[Any, ...] | None = None
    data_out_ports: tuple[Any, ...] | None = None
    exec_in_ports: tuple[str, ...] | None = None
    exec_out_ports: tuple[str, ...] | None = None


@dataclass(frozen=True)
class SetNodeStateFieldsOp:
    node_id: str
    state_fields: tuple[Any, ...]


@dataclass(frozen=True)
class MoveNodeOp:
    node_id: str
    pos: tuple[float, float]


@dataclass(frozen=True)
class SetUiOverrideOp:
    node_id: str
    key: str
    value: Any


GraphPatchOp = (
    CreateNodeOp
    | DeleteNodeOp
    | ConnectPortsOp
    | DisconnectPortsOp
    | SetNodeStateOp
    | SetNodeNameOp
    | SetNodePortsOp
    | SetNodeStateFieldsOp
    | MoveNodeOp
    | SetUiOverrideOp
)


@dataclass(frozen=True)
class GraphPatch:
    expected_revision: int | None
    ops: tuple[GraphPatchOp, ...]
    label: str = "automation patch"


@dataclass(frozen=True)
class GraphPatchPreview:
    expected_revision: int | None
    current_revision: int
    valid: bool
    changed_node_ids: tuple[str, ...] = ()
    compile_warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    def to_dict(self) -> JsonObject:
        return asdict(self)


@dataclass(frozen=True)
class RuntimeObservation:
    service_id: str
    node_id: str
    channel: str
    name: str
    value: Any
    ts_ms: int = 0

    def to_dict(self) -> JsonObject:
        return asdict(self)


@dataclass(frozen=True)
class ServiceRuntimeStatus:
    service_id: str
    service_class: str = ""
    running: bool = False
    active: bool | None = None
    alive: bool | None = None
    latest_monitor: JsonObject | None = None

    def to_dict(self) -> JsonObject:
        return asdict(self)


def decode_graph_patch(payload: Any) -> GraphPatch:
    if not isinstance(payload, dict):
        raise ValueError("graph patch must be a JSON object")
    expected_revision = _optional_int(payload.get("expectedRevision"))
    label = str(payload.get("label") or "automation patch").strip() or "automation patch"
    raw_ops = payload.get("ops")
    if not isinstance(raw_ops, list):
        raise ValueError("graph patch must contain an `ops` list")
    ops: list[GraphPatchOp] = []
    for index, raw_op in enumerate(raw_ops):
        if not isinstance(raw_op, dict):
            raise ValueError(f"graph patch op #{index} must be a JSON object")
        ops.append(_decode_graph_patch_op(raw_op, index=index))
    return GraphPatch(expected_revision=expected_revision, ops=tuple(ops), label=label)


def graph_patch_to_dict(patch: GraphPatch) -> JsonObject:
    ops: list[JsonObject] = []
    for op in patch.ops:
        ops.append(_graph_patch_op_to_dict(op))
    return {
        "expectedRevision": patch.expected_revision,
        "label": patch.label,
        "ops": ops,
    }


def _decode_graph_patch_op(payload: JsonObject, *, index: int) -> GraphPatchOp:
    kind = _required_str(payload, "op", index=index)
    if kind == "createNode":
        return CreateNodeOp(
            node_type=_required_str(payload, "nodeType", index=index),
            node_id=_optional_str(payload.get("nodeId")),
            name=_optional_str(payload.get("name")),
            pos=_tuple2(payload.get("pos"), default=(0.0, 0.0), label=f"op #{index} pos"),
            selected=bool(payload.get("selected", False)),
        )
    if kind == "deleteNode":
        return DeleteNodeOp(node_id=_required_str(payload, "nodeId", index=index))
    if kind == "connectPorts":
        return ConnectPortsOp(
            from_node_id=_required_str(payload, "fromNodeId", index=index),
            from_port=_required_str(payload, "fromPort", index=index),
            to_node_id=_required_str(payload, "toNodeId", index=index),
            to_port=_required_str(payload, "toPort", index=index),
        )
    if kind == "disconnectPorts":
        return DisconnectPortsOp(
            from_node_id=_required_str(payload, "fromNodeId", index=index),
            from_port=_required_str(payload, "fromPort", index=index),
            to_node_id=_required_str(payload, "toNodeId", index=index),
            to_port=_required_str(payload, "toPort", index=index),
        )
    if kind == "setNodeState":
        if "value" not in payload:
            raise ValueError(f"graph patch op #{index} missing required field `value`")
        return SetNodeStateOp(
            node_id=_required_str(payload, "nodeId", index=index),
            field=_required_str(payload, "field", index=index),
            value=payload.get("value"),
        )
    if kind == "setNodeName":
        return SetNodeNameOp(
            node_id=_required_str(payload, "nodeId", index=index),
            name=_required_str(payload, "name", index=index),
        )
    if kind == "setNodePorts":
        return SetNodePortsOp(
            node_id=_required_str(payload, "nodeId", index=index),
            data_in_ports=_optional_tuple(payload, "dataInPorts", index=index),
            data_out_ports=_optional_tuple(payload, "dataOutPorts", index=index),
            exec_in_ports=_optional_str_tuple(payload, "execInPorts", index=index),
            exec_out_ports=_optional_str_tuple(payload, "execOutPorts", index=index),
        )
    if kind == "setNodeStateFields":
        state_fields = _optional_tuple(payload, "stateFields", index=index)
        if state_fields is None:
            raise ValueError(f"graph patch op #{index} missing required field `stateFields`")
        return SetNodeStateFieldsOp(
            node_id=_required_str(payload, "nodeId", index=index),
            state_fields=state_fields,
        )
    if kind == "moveNode":
        return MoveNodeOp(
            node_id=_required_str(payload, "nodeId", index=index),
            pos=_tuple2(payload.get("pos"), default=None, label=f"op #{index} pos"),
        )
    if kind == "setUiOverride":
        if "value" not in payload:
            raise ValueError(f"graph patch op #{index} missing required field `value`")
        return SetUiOverrideOp(
            node_id=_required_str(payload, "nodeId", index=index),
            key=_required_str(payload, "key", index=index),
            value=payload.get("value"),
        )
    raise ValueError(f"graph patch op #{index} has unsupported op: {kind!r}")


def _graph_patch_op_to_dict(op: GraphPatchOp) -> JsonObject:
    if isinstance(op, CreateNodeOp):
        return {
            "op": "createNode",
            "nodeType": op.node_type,
            "nodeId": op.node_id,
            "name": op.name,
            "pos": [op.pos[0], op.pos[1]],
            "selected": op.selected,
        }
    if isinstance(op, DeleteNodeOp):
        return {"op": "deleteNode", "nodeId": op.node_id}
    if isinstance(op, ConnectPortsOp):
        return {
            "op": "connectPorts",
            "fromNodeId": op.from_node_id,
            "fromPort": op.from_port,
            "toNodeId": op.to_node_id,
            "toPort": op.to_port,
        }
    if isinstance(op, DisconnectPortsOp):
        return {
            "op": "disconnectPorts",
            "fromNodeId": op.from_node_id,
            "fromPort": op.from_port,
            "toNodeId": op.to_node_id,
            "toPort": op.to_port,
        }
    if isinstance(op, SetNodeStateOp):
        return {"op": "setNodeState", "nodeId": op.node_id, "field": op.field, "value": op.value}
    if isinstance(op, SetNodeNameOp):
        return {"op": "setNodeName", "nodeId": op.node_id, "name": op.name}
    if isinstance(op, SetNodePortsOp):
        payload: JsonObject = {"op": "setNodePorts", "nodeId": op.node_id}
        if op.data_in_ports is not None:
            payload["dataInPorts"] = list(op.data_in_ports)
        if op.data_out_ports is not None:
            payload["dataOutPorts"] = list(op.data_out_ports)
        if op.exec_in_ports is not None:
            payload["execInPorts"] = list(op.exec_in_ports)
        if op.exec_out_ports is not None:
            payload["execOutPorts"] = list(op.exec_out_ports)
        return payload
    if isinstance(op, SetNodeStateFieldsOp):
        return {"op": "setNodeStateFields", "nodeId": op.node_id, "stateFields": list(op.state_fields)}
    if isinstance(op, MoveNodeOp):
        return {"op": "moveNode", "nodeId": op.node_id, "pos": [op.pos[0], op.pos[1]]}
    if isinstance(op, SetUiOverrideOp):
        return {"op": "setUiOverride", "nodeId": op.node_id, "key": op.key, "value": op.value}
    raise TypeError(f"unsupported graph patch op type: {type(op).__name__}")


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("expectedRevision must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("expectedRevision must be an integer") from exc


def _optional_str(value: Any) -> str:
    return str(value or "").strip()


def _optional_tuple(payload: JsonObject, key: str, *, index: int) -> tuple[Any, ...] | None:
    if key not in payload:
        return None
    value = payload.get(key)
    if not isinstance(value, list):
        raise ValueError(f"graph patch op #{index} field `{key}` must be a list")
    return tuple(value)


def _optional_str_tuple(payload: JsonObject, key: str, *, index: int) -> tuple[str, ...] | None:
    raw = _optional_tuple(payload, key, index=index)
    if raw is None:
        return None
    out: list[str] = []
    for item_index, item in enumerate(raw):
        text = str(item or "").strip()
        if not text:
            raise ValueError(f"graph patch op #{index} field `{key}` item #{item_index} must be a non-empty string")
        out.append(text)
    return tuple(out)


def _required_str(payload: JsonObject, key: str, *, index: int) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ValueError(f"graph patch op #{index} missing required field `{key}`")
    return value


def _tuple2(value: Any, *, default: tuple[float, float] | None, label: str) -> tuple[float, float]:
    if value is None:
        if default is None:
            raise ValueError(f"{label} is required")
        return default
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{label} must be a two-item array")
    try:
        return (float(value[0]), float(value[1]))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must contain numbers") from exc
