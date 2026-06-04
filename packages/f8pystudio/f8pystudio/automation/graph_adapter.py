from __future__ import annotations

import copy
import logging
from typing import Any

from f8pysdk.specs import F8OperatorSpec, F8ServiceSpec

from f8pystudio.bridge.json_codec import coerce_json_value
from f8pystudio.nodegraph.runtime_compiler import compile_runtime_graphs_from_studio
from f8pystudio.nodegraph.session_schema import extract_layout

from .domain import (
    ConnectPortsOp,
    CreateNodeOp,
    DeleteNodeOp,
    DisconnectPortsOp,
    GraphEdgeSnapshot,
    GraphNodeSnapshot,
    GraphPatch,
    GraphPatchPreview,
    GraphPortSnapshot,
    GraphSnapshot,
    GraphStateFieldSnapshot,
    MoveNodeOp,
    SetNodeNameOp,
    SetNodeStateOp,
    SetUiOverrideOp,
)

logger = logging.getLogger(__name__)
_GRAPH_READ_ERRORS = (AttributeError, KeyError, RuntimeError, TypeError, ValueError)
_GRAPH_MUTATION_ERRORS = (AttributeError, KeyError, RuntimeError, TypeError, ValueError)


class StudioGraphAutomationAdapter:
    def __init__(self, studio_graph: Any) -> None:
        self._graph = studio_graph

    def revision(self) -> int:
        undo_stack = self._graph._undo_stack
        return int(undo_stack.index())

    def snapshot(self) -> GraphSnapshot:
        nodes = sorted(list(self._graph.all_nodes() or []), key=_node_id)
        node_snapshots = tuple(_node_snapshot(node) for node in nodes)
        edge_snapshots = tuple(_collect_edge_snapshots(nodes))
        selected_ids = tuple(
            sorted(
                node_id
                for node_id in (_node_id(node) for node in list(self._graph.selected_nodes() or []))
                if node_id
            )
        )
        return GraphSnapshot(
            revision=self.revision(),
            node_count=len(node_snapshots),
            edge_count=len(edge_snapshots),
            selected_node_ids=selected_ids,
            nodes=node_snapshots,
            edges=edge_snapshots,
        )

    def node_catalog(self) -> dict[str, Any]:
        node_factory = self._graph.node_factory
        nodes_by_type = dict(node_factory.nodes or {})
        items: list[dict[str, Any]] = []
        for node_type in sorted(nodes_by_type.keys()):
            node_cls = nodes_by_type.get(node_type)
            spec = _spec_from_node_class(node_cls)
            label = _node_class_label(node_cls, fallback=node_type)
            items.append(
                {
                    "nodeType": str(node_type),
                    "label": label,
                    "kind": _spec_kind(spec),
                    "serviceClass": _spec_service_class(spec),
                    "operatorClass": _spec_operator_class(spec),
                    "inputs": _spec_ports(spec, direction="in"),
                    "outputs": _spec_ports(spec, direction="out"),
                    "stateFields": _spec_state_fields(spec),
                }
            )
        return {"nodes": items}

    def preview_patch(self, patch: GraphPatch) -> GraphPatchPreview:
        current_revision = self.revision()
        errors = self._validate_patch_revision(patch, current_revision=current_revision)
        if errors:
            return GraphPatchPreview(
                expected_revision=patch.expected_revision,
                current_revision=current_revision,
                valid=False,
                errors=tuple(errors),
            )

        session_payload = copy.deepcopy(self._graph.serialize_session())
        before_revision = current_revision
        try:
            self.apply_patch(patch, validate_revision=False, push_undo=False)
            compiled = compile_runtime_graphs_from_studio(self._graph)
            changed_node_ids = tuple(sorted(_changed_node_ids(patch)))
            return GraphPatchPreview(
                expected_revision=patch.expected_revision,
                current_revision=before_revision,
                valid=True,
                changed_node_ids=changed_node_ids,
                compile_warnings=tuple(compiled.warnings or ()),
                errors=(),
            )
        except _GRAPH_MUTATION_ERRORS as exc:
            return GraphPatchPreview(
                expected_revision=patch.expected_revision,
                current_revision=before_revision,
                valid=False,
                errors=(str(exc),),
            )
        finally:
            try:
                self._graph.load_session_payload(session_payload)
            except _GRAPH_MUTATION_ERRORS:
                logger.exception("failed to restore graph after automation preview")

    def apply_patch(
        self,
        patch: GraphPatch,
        *,
        validate_revision: bool = True,
        push_undo: bool = True,
    ) -> GraphPatchPreview:
        current_revision = self.revision()
        errors = self._validate_patch_revision(patch, current_revision=current_revision) if validate_revision else []
        if errors:
            return GraphPatchPreview(
                expected_revision=patch.expected_revision,
                current_revision=current_revision,
                valid=False,
                errors=tuple(errors),
            )
        begin_macro = bool(push_undo)
        if begin_macro:
            self._graph.begin_undo(str(patch.label or "automation patch"))
        try:
            for op in patch.ops:
                self._validate_op(op)
                self._apply_op(op, push_undo=push_undo)
        finally:
            if begin_macro:
                self._graph.end_undo()
        compiled = compile_runtime_graphs_from_studio(self._graph)
        return GraphPatchPreview(
            expected_revision=patch.expected_revision,
            current_revision=self.revision(),
            valid=True,
            changed_node_ids=tuple(sorted(_changed_node_ids(patch))),
            compile_warnings=tuple(compiled.warnings or ()),
        )

    def compile_graph(self) -> dict[str, Any]:
        compiled = compile_runtime_graphs_from_studio(self._graph)
        global_graph = compiled.global_graph
        services = [
            {
                "serviceId": str(service.serviceId),
                "serviceClass": str(service.serviceClass),
                "nodeCount": len(list(compiled.per_service.get(str(service.serviceId), global_graph).nodes or [])),
                "edgeCount": len(list(compiled.per_service.get(str(service.serviceId), global_graph).edges or [])),
            }
            for service in list(global_graph.services or [])
        ]
        return {
            "revision": self.revision(),
            "graphId": str(global_graph.graphId),
            "rungraphRevision": str(global_graph.revision),
            "serviceCount": len(list(global_graph.services or [])),
            "nodeCount": len(list(global_graph.nodes or [])),
            "edgeCount": len(list(global_graph.edges or [])),
            "services": services,
            "warnings": list(compiled.warnings or ()),
        }

    def session_payload(self) -> dict[str, Any]:
        return dict(self._graph.serialize_session())

    def _validate_patch_revision(self, patch: GraphPatch, *, current_revision: int) -> list[str]:
        expected = patch.expected_revision
        if expected is None:
            return []
        if int(expected) != int(current_revision):
            return [f"stale graph revision: expected {expected}, current {current_revision}"]
        return []

    def _validate_op(self, op: object) -> None:
        if isinstance(op, CreateNodeOp):
            if op.node_type not in self._graph.node_factory.nodes:
                raise ValueError(f"unknown node type: {op.node_type}")
            if op.node_id and self._graph.get_node_by_id(op.node_id) is not None:
                raise ValueError(f"node already exists: {op.node_id}")
            return
        if isinstance(op, DeleteNodeOp):
            self._require_node(op.node_id)
            return
        if isinstance(op, ConnectPortsOp):
            self._require_output_port(op.from_node_id, op.from_port)
            self._require_input_port(op.to_node_id, op.to_port)
            return
        if isinstance(op, DisconnectPortsOp):
            out_port = self._require_output_port(op.from_node_id, op.from_port)
            in_port = self._require_input_port(op.to_node_id, op.to_port)
            if in_port not in list(out_port.connected_ports() or []):
                raise ValueError(
                    f"ports are not connected: {op.from_node_id}.{op.from_port} -> {op.to_node_id}.{op.to_port}"
                )
            return
        if isinstance(op, SetNodeStateOp):
            node = self._require_node(op.node_id)
            if not _node_has_state_field(node, op.field):
                raise ValueError(f"node {op.node_id} has no state field {op.field!r}")
            coerce_json_value(op.value)
            return
        if isinstance(op, SetNodeNameOp):
            self._require_node(op.node_id)
            if not op.name.strip():
                raise ValueError("node name cannot be empty")
            return
        if isinstance(op, MoveNodeOp):
            self._require_node(op.node_id)
            return
        if isinstance(op, SetUiOverrideOp):
            self._require_node(op.node_id)
            if not op.key.strip():
                raise ValueError("ui override key cannot be empty")
            coerce_json_value(op.value)
            return
        raise TypeError(f"unsupported automation patch op: {type(op).__name__}")

    def _apply_op(self, op: object, *, push_undo: bool) -> None:
        if isinstance(op, CreateNodeOp):
            node = self._graph.create_node(
                op.node_type,
                name=op.name or None,
                selected=op.selected,
                pos=op.pos,
                push_undo=push_undo,
                begin_undo_macro=False,
            )
            if node is None:
                raise ValueError(f"failed to create node: {op.node_type}")
            if op.node_id:
                _assign_existing_node_id(self._graph, node, op.node_id)
            return
        if isinstance(op, DeleteNodeOp):
            self._graph.delete_node(self._require_node(op.node_id), push_undo=push_undo)
            return
        if isinstance(op, ConnectPortsOp):
            out_port = self._require_output_port(op.from_node_id, op.from_port)
            in_port = self._require_input_port(op.to_node_id, op.to_port)
            out_port.connect_to(in_port, push_undo=push_undo, emit_signal=True)
            return
        if isinstance(op, DisconnectPortsOp):
            out_port = self._require_output_port(op.from_node_id, op.from_port)
            in_port = self._require_input_port(op.to_node_id, op.to_port)
            out_port.disconnect_from(in_port, push_undo=push_undo, emit_signal=True)
            return
        if isinstance(op, SetNodeStateOp):
            self._require_node(op.node_id).set_property(op.field, coerce_json_value(op.value), push_undo=push_undo)
            return
        if isinstance(op, SetNodeNameOp):
            self._require_node(op.node_id).set_property("name", op.name, push_undo=push_undo)
            return
        if isinstance(op, MoveNodeOp):
            node = self._require_node(op.node_id)
            node.set_property("pos", [float(op.pos[0]), float(op.pos[1])], push_undo=push_undo)
            return
        if isinstance(op, SetUiOverrideOp):
            node = self._require_node(op.node_id)
            ui = dict(node.ui_overrides())
            if op.value is None:
                ui.pop(op.key, None)
            else:
                ui[op.key] = coerce_json_value(op.value)
            node.set_ui_overrides(ui, rebuild=True)
            return
        raise TypeError(f"unsupported automation patch op: {type(op).__name__}")

    def _require_node(self, node_id: str) -> Any:
        node = self._graph.get_node_by_id(str(node_id or "").strip())
        if node is None:
            raise ValueError(f"unknown nodeId: {node_id}")
        return node

    def _require_input_port(self, node_id: str, port_name: str) -> Any:
        node = self._require_node(node_id)
        port = _resolve_port(node.inputs(), str(port_name), is_input=True)
        if port is None:
            raise ValueError(f"unknown input port: {node_id}.{port_name}")
        return port

    def _require_output_port(self, node_id: str, port_name: str) -> Any:
        node = self._require_node(node_id)
        port = _resolve_port(node.outputs(), str(port_name), is_input=False)
        if port is None:
            raise ValueError(f"unknown output port: {node_id}.{port_name}")
        return port


def _node_snapshot(node: Any) -> GraphNodeSnapshot:
    spec = _node_spec(node)
    return GraphNodeSnapshot(
        node_id=_node_id(node),
        node_type=_node_type(node),
        name=_node_name(node),
        kind=_spec_kind(spec),
        service_class=_spec_service_class(spec),
        operator_class=_spec_operator_class(spec),
        pos=_node_pos(node),
        selected=_node_selected(node),
        inputs=tuple(_port_snapshot(port, direction="in") for port in _node_ports(node, is_input=True)),
        outputs=tuple(_port_snapshot(port, direction="out") for port in _node_ports(node, is_input=False)),
        state_fields=tuple(_state_field_snapshot(field) for field in _spec_state_field_objects(spec)),
    )


def _collect_edge_snapshots(nodes: list[Any]) -> list[GraphEdgeSnapshot]:
    edges: list[GraphEdgeSnapshot] = []
    for node in nodes:
        source_id = _node_id(node)
        for out_port in _node_ports(node, is_input=False):
            out_name = _port_name(out_port)
            for in_port in list(out_port.connected_ports() or []):
                target_node = in_port.node()
                edges.append(
                    GraphEdgeSnapshot(
                        edge_kind=_edge_kind_from_port_names(out_name, _port_name(in_port)),
                        from_node_id=source_id,
                        from_port=out_name,
                        to_node_id=_node_id(target_node),
                        to_port=_port_name(in_port),
                    )
                )
    return sorted(edges, key=lambda edge: (edge.from_node_id, edge.from_port, edge.to_node_id, edge.to_port))


def _node_ports(node: Any, *, is_input: bool) -> list[Any]:
    try:
        ports = node.inputs() if bool(is_input) else node.outputs()
    except _GRAPH_READ_ERRORS:
        return []
    if ports is None:
        return []
    if isinstance(ports, dict):
        return list(ports.values())
    try:
        return list(ports)
    except _GRAPH_READ_ERRORS:
        return []


def _port_snapshot(port: Any, *, direction: str) -> GraphPortSnapshot:
    name = _port_name(port)
    return GraphPortSnapshot(name=name, kind=_edge_kind_from_port_names(name, name), direction=direction)


def _state_field_snapshot(field: Any) -> GraphStateFieldSnapshot:
    return GraphStateFieldSnapshot(
        name=str(field.name or "").strip(),
        access=_state_access_text(field.access),
        label=str(field.label or ""),
        description=str(field.description or ""),
    )


def _node_id(node: Any) -> str:
    try:
        return str(node.id or "").strip()
    except _GRAPH_READ_ERRORS:
        return ""


def _node_type(node: Any) -> str:
    try:
        return str(node.type_ or "").strip()
    except _GRAPH_READ_ERRORS:
        return ""


def _node_name(node: Any) -> str:
    try:
        return str(node.name() or "").strip()
    except _GRAPH_READ_ERRORS:
        return ""


def _node_pos(node: Any) -> tuple[float, float]:
    try:
        pos = node.pos()
        return (float(pos[0]), float(pos[1]))
    except _GRAPH_READ_ERRORS:
        return (0.0, 0.0)


def _node_selected(node: Any) -> bool:
    try:
        return bool(node.selected())
    except _GRAPH_READ_ERRORS:
        try:
            return bool(node.model.selected)
        except _GRAPH_READ_ERRORS:
            return False


def _node_spec(node: Any) -> F8OperatorSpec | F8ServiceSpec | None:
    try:
        spec = node.spec
    except _GRAPH_READ_ERRORS:
        return None
    if isinstance(spec, (F8OperatorSpec, F8ServiceSpec)):
        return spec
    return None


def _port_name(port: Any) -> str:
    try:
        return str(port.name() or "").strip()
    except _GRAPH_READ_ERRORS:
        return ""


def _edge_kind_from_port_names(left: str, right: str) -> str:
    text = f"{left} {right}"
    if "[E]" in text:
        return "exec"
    if "[S]" in text or "[C]" in text:
        return "state"
    if "[D]" in text:
        return "data"
    return ""


def _node_has_state_field(node: Any, field_name: str) -> bool:
    spec = _node_spec(node)
    if spec is None:
        return False
    needle = str(field_name or "").strip()
    for field in list(spec.stateFields or []):
        if str(field.name or "").strip() == needle:
            return True
    return False


def _assign_existing_node_id(graph: Any, node: Any, node_id: str) -> None:
    normalized_node_id = str(node_id or "").strip()
    if not normalized_node_id:
        return
    old_id = _node_id(node)
    if old_id == normalized_node_id:
        return
    model_nodes = graph.model.nodes
    if old_id in model_nodes:
        model_nodes.pop(old_id, None)
        model_nodes[normalized_node_id] = node
    node.model.id = normalized_node_id
    node.view.id = normalized_node_id
    model = node.model
    if "operatorId" in model.properties or "operatorId" in model.custom_properties:
        node.set_property("operatorId", normalized_node_id, push_undo=False)
    if "svcId" in model.properties or "svcId" in model.custom_properties:
        node.set_property("svcId", normalized_node_id, push_undo=False)


def _changed_node_ids(patch: GraphPatch) -> set[str]:
    out: set[str] = set()
    for op in patch.ops:
        if isinstance(op, CreateNodeOp):
            if op.node_id:
                out.add(op.node_id)
        elif isinstance(op, DeleteNodeOp):
            out.add(op.node_id)
        elif isinstance(op, ConnectPortsOp):
            out.add(op.from_node_id)
            out.add(op.to_node_id)
        elif isinstance(op, DisconnectPortsOp):
            out.add(op.from_node_id)
            out.add(op.to_node_id)
        elif isinstance(op, SetNodeStateOp):
            out.add(op.node_id)
        elif isinstance(op, SetNodeNameOp):
            out.add(op.node_id)
        elif isinstance(op, MoveNodeOp):
            out.add(op.node_id)
        elif isinstance(op, SetUiOverrideOp):
            out.add(op.node_id)
    return out


def _spec_from_node_class(node_cls: Any) -> F8OperatorSpec | F8ServiceSpec | None:
    if node_cls is None:
        return None
    try:
        spec = node_cls.SPEC_TEMPLATE
    except (AttributeError, RuntimeError, TypeError):
        return None
    if isinstance(spec, (F8OperatorSpec, F8ServiceSpec)):
        return spec
    return None


def _node_class_label(node_cls: Any, *, fallback: str) -> str:
    if node_cls is None:
        return str(fallback)
    try:
        name = node_cls.NODE_NAME
    except (AttributeError, RuntimeError, TypeError):
        name = fallback
    return str(name or fallback)


def _spec_kind(spec: F8OperatorSpec | F8ServiceSpec | None) -> str:
    if isinstance(spec, F8OperatorSpec):
        return "operator"
    if isinstance(spec, F8ServiceSpec):
        return "service"
    return ""


def _spec_service_class(spec: F8OperatorSpec | F8ServiceSpec | None) -> str:
    if spec is None:
        return ""
    return str(spec.serviceClass or "")


def _spec_operator_class(spec: F8OperatorSpec | F8ServiceSpec | None) -> str:
    if isinstance(spec, F8OperatorSpec):
        return str(spec.operatorClass or "")
    return ""


def _spec_ports(spec: F8OperatorSpec | F8ServiceSpec | None, *, direction: str) -> list[dict[str, Any]]:
    if spec is None:
        return []
    ports = list(spec.dataInPorts or []) if direction == "in" else list(spec.dataOutPorts or [])
    out = [{"name": str(port.name or ""), "kind": "data", "description": str(port.description or "")} for port in ports]
    if isinstance(spec, F8OperatorSpec):
        exec_ports = list(spec.execInPorts or []) if direction == "in" else list(spec.execOutPorts or [])
        out.extend({"name": str(port), "kind": "exec", "description": ""} for port in exec_ports)
    return out


def _spec_state_fields(spec: F8OperatorSpec | F8ServiceSpec | None) -> list[dict[str, Any]]:
    if spec is None:
        return []
    return [
        {
            "name": str(field.name or ""),
            "access": _state_access_text(field.access),
            "label": str(field.label or ""),
            "description": str(field.description or ""),
        }
        for field in _spec_state_field_objects(spec)
    ]


def _spec_state_field_objects(spec: F8OperatorSpec | F8ServiceSpec | None) -> list[Any]:
    if isinstance(spec, (F8OperatorSpec, F8ServiceSpec)):
        return list(spec.stateFields or [])
    return []


def _state_access_text(access: Any) -> str:
    try:
        return str(access.value or "")
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return str(access or "")


def _resolve_port(ports: Any, port_name: str, *, is_input: bool) -> Any | None:
    normalized_name = str(port_name or "").strip()
    if not normalized_name:
        return None
    if ports is None:
        return None
    if not isinstance(ports, dict):
        return None
    direct = ports.get(normalized_name)
    if direct is not None:
        return direct

    candidate_names = (
        f"[D]{normalized_name}" if bool(is_input) else f"{normalized_name}[D]",
        f"[S]{normalized_name}" if bool(is_input) else f"{normalized_name}[S]",
        f"[E]{normalized_name}" if bool(is_input) else f"{normalized_name}[E]",
    )
    for candidate_name in candidate_names:
        port = ports.get(candidate_name)
        if port is not None:
            return port
    return None


def layout_from_session_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return dict(extract_layout(payload))
