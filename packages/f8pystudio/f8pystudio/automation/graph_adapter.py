from __future__ import annotations

import copy
import hashlib
import json
import logging
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from qtpy import QtCore, QtWidgets

from f8pysdk.codec import dump_json, validate_as
from f8pysdk.specs import F8OperatorSpec, F8ServiceSpec
from f8pysdk.specs import F8DataPortSpec, F8StateSpec, can_add, can_delete, can_edit_existing

from f8pystudio.bridge.json_codec import coerce_json_value
from f8pystudio.nodegraph.spec_mutations import set_ports, set_state_fields
from f8pystudio.nodegraph.runtime_compiler import compile_runtime_graphs_from_studio
from f8pystudio.nodegraph.edge_rules import raw_data_port_name
from f8pystudio.nodegraph.session_schema import extract_layout
from f8pystudio.studio_specs.identifiers import SERVICE_CLASS as STUDIO_SERVICE_CLASS
from f8pystudio.studio_specs.identifiers import STUDIO_SERVICE_ID

if TYPE_CHECKING:
    from f8pystudio.nodegraph.container_basenode import F8StudioContainerBaseNode
    from f8pystudio.nodegraph.operator_basenode import F8StudioOperatorBaseNode

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
    SetNodePortsOp,
    SetNodeStateFieldsOp,
    SetNodeStateOp,
    SetUiOverrideOp,
)

logger = logging.getLogger(__name__)
_GRAPH_READ_ERRORS = (AttributeError, KeyError, RuntimeError, TypeError, ValueError)
_GRAPH_MUTATION_ERRORS = (AttributeError, KeyError, RuntimeError, TypeError, ValueError)
_MAX_NODE_DETAIL_TEXT = 12000
_RUNTIME_BINDING_STATE_FIELD = "svcId"
_AUTO_CONTAINER_PADDING = 80.0
_AUTO_CONTAINER_GEOMETRY_EPSILON = 0.5


class _RuntimeServiceBindingChangeCommand(QtWidgets.QUndoCommand):
    def __init__(self, node: Any, service_id: str) -> None:
        super().__init__('property "{}:{}"'.format(_node_name(node), _RUNTIME_BINDING_STATE_FIELD))
        self._node = node
        self._old_service_id = _node_service_id(node)
        self._new_service_id = str(service_id or "").strip()

    def undo(self) -> None:
        _apply_runtime_binding_service_id(self._node, self._old_service_id)
        _sync_operator_container_reference_for_service_id(self._node)

    def redo(self) -> None:
        _apply_runtime_binding_service_id(self._node, self._new_service_id)
        _sync_operator_container_reference_for_service_id(self._node)


class _ContainerGeometryChangeCommand(QtWidgets.QUndoCommand):
    def __init__(
        self,
        *,
        container: F8StudioContainerBaseNode,
        before: QtCore.QRectF,
        after: QtCore.QRectF,
    ) -> None:
        super().__init__('resize service container "{}"'.format(_node_name(container)))
        self._container = container
        self._before = QtCore.QRectF(before)
        self._after = QtCore.QRectF(after)

    def undo(self) -> None:
        _apply_container_geometry_without_child_translation(self._container, self._before)

    def redo(self) -> None:
        _apply_container_geometry_without_child_translation(self._container, self._after)


class _NodeSpecChangeCommand(QtWidgets.QUndoCommand):
    def __init__(
        self, *, node: Any, before: F8OperatorSpec | F8ServiceSpec, after: F8OperatorSpec | F8ServiceSpec
    ) -> None:
        super().__init__('spec "{}"'.format(_node_name(node)))
        self._node = node
        self._before = _copy_node_spec(before)
        self._after = _copy_node_spec(after)
        self._before_custom_properties = _copy_node_custom_properties(node)
        self._after_custom_properties: dict[str, Any] | None = None
        self._first_redo = True

    def undo(self) -> None:
        _apply_node_spec(self._node, _copy_node_spec(self._before))
        _restore_node_custom_properties(self._node, self._before_custom_properties)

    def redo(self) -> None:
        _apply_node_spec(self._node, _copy_node_spec(self._after))
        if bool(self._first_redo):
            self._after_custom_properties = _copy_node_custom_properties(self._node)
            self._first_redo = False
            return
        if self._after_custom_properties is not None:
            _restore_node_custom_properties(self._node, self._after_custom_properties)


def _is_studio_operator_node(node: Any) -> bool:
    from f8pystudio.nodegraph.operator_basenode import F8StudioOperatorBaseNode

    return isinstance(node, F8StudioOperatorBaseNode)


def _is_studio_container_node(node: Any) -> bool:
    from f8pystudio.nodegraph.container_basenode import F8StudioContainerBaseNode

    return isinstance(node, F8StudioContainerBaseNode)


class StudioGraphAutomationAdapter:
    def __init__(self, studio_graph: Any) -> None:
        self._graph = studio_graph

    def revision(self) -> int:
        return _session_revision(self._graph.serialize_session())

    def snapshot(self) -> GraphSnapshot:
        nodes = sorted(list(self._graph.all_nodes() or []), key=_node_id)
        node_snapshots = tuple(_node_snapshot(node) for node in nodes)
        edge_snapshots = tuple(_collect_edge_snapshots(nodes))
        selected_ids = tuple(
            sorted(
                node_id for node_id in (_node_id(node) for node in list(self._graph.selected_nodes() or [])) if node_id
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

    def output_data_port_spec(self, *, node_id: str, port_name: str) -> F8DataPortSpec:
        node = self._require_node(node_id)
        spec = _node_spec(node)
        if spec is None:
            raise ValueError(f"node has no typed spec: {node_id}")
        normalized_port_name = raw_data_port_name(port_name, is_input=False) or str(port_name or "").strip()
        for port in list(spec.dataOutPorts or []):
            if isinstance(port, F8DataPortSpec) and str(port.name or "").strip() == normalized_port_name:
                return port
        raise ValueError(f"output data port not found: {node_id}.{port_name}")

    def find_nodes(
        self,
        *,
        query: str = "",
        node_id: str = "",
        node_type: str = "",
        kind: str = "",
        service_class: str = "",
        operator_class: str = "",
        selected_only: bool = False,
        limit: int = 50,
    ) -> dict[str, Any]:
        query_text = str(query or "").strip().lower()
        exact_node_id = str(node_id or "").strip()
        exact_node_type = str(node_type or "").strip()
        exact_kind = str(kind or "").strip()
        exact_service_class = str(service_class or "").strip()
        exact_operator_class = str(operator_class or "").strip()
        max_items = max(1, min(int(limit), 500))

        matches: list[dict[str, Any]] = []
        total_matches = 0
        for node in sorted(list(self._graph.all_nodes() or []), key=_node_id):
            snapshot = _node_snapshot(node)
            if exact_node_id and snapshot.node_id != exact_node_id:
                continue
            if exact_node_type and snapshot.node_type != exact_node_type:
                continue
            if exact_kind and snapshot.kind != exact_kind:
                continue
            if exact_service_class and snapshot.service_class != exact_service_class:
                continue
            if exact_operator_class and snapshot.operator_class != exact_operator_class:
                continue
            if bool(selected_only) and not snapshot.selected:
                continue
            if query_text and query_text not in _node_search_text(snapshot):
                continue

            total_matches += 1
            if len(matches) >= max_items:
                continue
            matches.append(_node_snapshot_to_dict(snapshot))

        return {
            "nodes": matches,
            "count": len(matches),
            "totalMatches": total_matches,
            "truncated": total_matches > len(matches),
            "revision": self.revision(),
        }

    def node_detail(self, node_id: str) -> dict[str, Any]:
        node = self._require_node(node_id)
        node_snapshot = _node_snapshot(node)
        return {
            "node": _node_snapshot_to_dict(node_snapshot),
            "runtimeBinding": _node_runtime_binding(node),
            "stateValues": _node_state_values(node),
            "properties": _node_properties_payload(node),
            "ui": _node_ui_payload(node),
            "spec": _spec_to_json(_node_spec(node)),
            "connections": self.connections(node_id=node_snapshot.node_id, direction="both", limit=500)["connections"],
        }

    def connections(self, *, node_id: str = "", direction: str = "both", limit: int = 200) -> dict[str, Any]:
        snapshot = self.snapshot()
        target_node_id = str(node_id or "").strip()
        resolved_direction = str(direction or "both").strip().lower()
        if resolved_direction not in {"both", "incoming", "outgoing"}:
            raise ValueError("direction must be both, incoming, or outgoing")
        max_items = max(1, min(int(limit), 2000))

        matches: list[dict[str, Any]] = []
        total_matches = 0
        for edge in snapshot.edges:
            incoming = bool(target_node_id) and edge.to_node_id == target_node_id
            outgoing = bool(target_node_id) and edge.from_node_id == target_node_id
            if target_node_id:
                if resolved_direction == "incoming" and not incoming:
                    continue
                if resolved_direction == "outgoing" and not outgoing:
                    continue
                if resolved_direction == "both" and not (incoming or outgoing):
                    continue
            total_matches += 1
            if len(matches) >= max_items:
                continue
            matches.append(asdict(edge))
        return {
            "connections": matches,
            "count": len(matches),
            "totalMatches": total_matches,
            "truncated": total_matches > len(matches),
            "revision": snapshot.revision,
        }

    def diagnostics(self) -> dict[str, Any]:
        snapshot = self.snapshot()
        issues: list[dict[str, Any]] = []
        if snapshot.node_count == 0:
            issues.append(
                {
                    "severity": "info",
                    "code": "graph_empty",
                    "message": "The graph has no nodes.",
                    "details": {},
                }
            )

        nodes = list(self._graph.all_nodes() or [])
        service_nodes_by_id: dict[str, Any] = {}
        for node in nodes:
            spec = _node_spec(node)
            if isinstance(spec, F8ServiceSpec):
                service_nodes_by_id[_node_id(node)] = node

        for node in nodes:
            spec = _node_spec(node)
            if not isinstance(spec, F8OperatorSpec):
                continue
            node_id = _node_id(node)
            service_class = str(spec.serviceClass or "").strip()
            if service_class == STUDIO_SERVICE_CLASS:
                service_id = str(_node_runtime_binding(node).get("serviceId") or "").strip()
                if service_id != STUDIO_SERVICE_ID:
                    issues.append(
                        {
                            "severity": "warning",
                            "code": "studio_operator_service_binding",
                            "message": "A Studio-local operator is not bound to the built-in Studio service.",
                            "nodeId": node_id,
                            "details": {"serviceId": service_id, "expectedServiceId": STUDIO_SERVICE_ID},
                        }
                    )
                continue

            service_id = str(_node_runtime_binding(node).get("serviceId") or "").strip()
            if not service_id:
                issues.append(
                    {
                        "severity": "warning",
                        "code": "operator_missing_service_container",
                        "message": "Operator node is not bound to a service container.",
                        "nodeId": node_id,
                        "details": {"serviceClass": service_class},
                    }
                )
                continue
            service_node = service_nodes_by_id.get(service_id)
            if service_node is None:
                issues.append(
                    {
                        "severity": "warning",
                        "code": "operator_service_container_missing",
                        "message": "Operator node references a service container that is not present in the graph.",
                        "nodeId": node_id,
                        "details": {"serviceId": service_id, "serviceClass": service_class},
                    }
                )
                continue
            service_spec = _node_spec(service_node)
            if isinstance(service_spec, F8ServiceSpec) and str(service_spec.serviceClass or "") != service_class:
                issues.append(
                    {
                        "severity": "warning",
                        "code": "operator_service_class_mismatch",
                        "message": "Operator node is bound to a service container with a different service class.",
                        "nodeId": node_id,
                        "details": {
                            "serviceId": service_id,
                            "operatorServiceClass": service_class,
                            "containerServiceClass": str(service_spec.serviceClass or ""),
                        },
                    }
                )

        compile_payload: dict[str, Any] | None = None
        try:
            compile_payload = self.compile_graph()
        except _GRAPH_MUTATION_ERRORS as exc:
            issues.append(
                {
                    "severity": "error",
                    "code": "graph_compile_failed",
                    "message": f"{type(exc).__name__}: {exc}",
                    "details": {},
                }
            )
        if compile_payload is not None:
            for warning in list(compile_payload.get("warnings") or []):
                issues.append(
                    {
                        "severity": "warning",
                        "code": "graph_compile_warning",
                        "message": str(warning),
                        "details": {},
                    }
                )

        return {
            "ok": not any(str(issue.get("severity") or "") == "error" for issue in issues),
            "revision": snapshot.revision,
            "summary": {
                "nodeCount": snapshot.node_count,
                "edgeCount": snapshot.edge_count,
                "issueCount": len(issues),
            },
            "issues": issues,
            "compile": compile_payload,
        }

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
            apply_preview = self.apply_patch(patch, validate_revision=False, push_undo=False)
            compiled = compile_runtime_graphs_from_studio(self._graph)
            return GraphPatchPreview(
                expected_revision=patch.expected_revision,
                current_revision=before_revision,
                valid=True,
                changed_node_ids=apply_preview.changed_node_ids,
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
            previous_skip_rebind = bool(self._graph.skip_post_load_container_rebind())
            try:
                self._graph.set_skip_post_load_container_rebind(True)
                self._graph.load_session_payload(session_payload)
            except _GRAPH_MUTATION_ERRORS:
                logger.exception("failed to restore graph after automation preview")
            finally:
                try:
                    self._graph.set_skip_post_load_container_rebind(previous_skip_rebind)
                except _GRAPH_MUTATION_ERRORS:
                    logger.exception("failed to restore post-load container rebind setting after automation preview")

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
            changed_node_ids = set(_changed_node_ids(patch))
            for op in patch.ops:
                self._validate_op(op)
                self._apply_op(op, push_undo=push_undo)
            changed_node_ids.update(self._auto_expand_service_containers_for_patch(patch, push_undo=push_undo))
        finally:
            if begin_macro:
                self._graph.end_undo()
        compiled = compile_runtime_graphs_from_studio(self._graph)
        return GraphPatchPreview(
            expected_revision=patch.expected_revision,
            current_revision=self.revision(),
            valid=True,
            changed_node_ids=tuple(sorted(changed_node_ids)),
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
            if not _node_has_state_field(node, op.field) and not _is_runtime_binding_state_field(op.field):
                raise ValueError(f"node {op.node_id} has no state field {op.field!r}")
            coerce_json_value(op.value)
            return
        if isinstance(op, SetNodeNameOp):
            self._require_node(op.node_id)
            if not op.name.strip():
                raise ValueError("node name cannot be empty")
            return
        if isinstance(op, SetNodePortsOp):
            self._validate_node_ports_op(op)
            return
        if isinstance(op, SetNodeStateFieldsOp):
            self._validate_node_state_fields_op(op)
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
            node = self._graph.create_node_for_session_load(
                op.node_type,
                name=op.name or None,
                selected=op.selected,
                pos=op.pos,
                push_undo=push_undo,
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
            _set_node_state_value(self._require_node(op.node_id), op.field, op.value, push_undo=push_undo)
            return
        if isinstance(op, SetNodeNameOp):
            self._require_node(op.node_id).set_property("name", op.name, push_undo=push_undo)
            return
        if isinstance(op, SetNodePortsOp):
            self._apply_node_ports_op(op, push_undo=push_undo)
            return
        if isinstance(op, SetNodeStateFieldsOp):
            self._apply_node_state_fields_op(op, push_undo=push_undo)
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

    def _auto_expand_service_containers_for_patch(self, patch: GraphPatch, *, push_undo: bool) -> set[str]:
        expanded_node_ids: set[str] = set()
        for node_id in sorted(_changed_node_ids(patch)):
            node = self._graph.get_node_by_id(node_id)
            if not _is_studio_operator_node(node):
                continue
            service_id = _node_service_id(node)
            if not service_id:
                continue

            operator_spec = _node_spec(node)
            if not isinstance(operator_spec, F8OperatorSpec):
                continue
            operator_service_class = str(operator_spec.serviceClass or "").strip()
            if not operator_service_class or operator_service_class == STUDIO_SERVICE_CLASS:
                continue

            container = self._graph.get_node_by_id(service_id)
            if not _is_studio_container_node(container):
                continue
            container_spec = _node_spec(container)
            if not isinstance(container_spec, F8ServiceSpec):
                continue
            if str(container_spec.serviceClass or "").strip() != operator_service_class:
                continue

            _sync_operator_container_reference(operator=node, container=container)
            if _expand_container_to_cover_operator(container=container, operator=node, push_undo=push_undo):
                expanded_node_ids.add(_node_id(container))
        return expanded_node_ids

    def _validate_node_ports_op(self, op: SetNodePortsOp) -> None:
        node = self._require_node(op.node_id)
        spec = _node_spec(node)
        if spec is None:
            raise ValueError(f"node {op.node_id} has no editable spec")
        if (
            op.data_in_ports is None
            and op.data_out_ports is None
            and op.exec_in_ports is None
            and op.exec_out_ports is None
        ):
            raise ValueError("setNodePorts requires at least one port collection")
        if op.data_in_ports is not None:
            _validate_port_edit_policy(spec, collection="dataInPorts")
            _decode_data_port_specs(op.data_in_ports, label="dataInPorts")
        if op.data_out_ports is not None:
            _validate_port_edit_policy(spec, collection="dataOutPorts")
            _decode_data_port_specs(op.data_out_ports, label="dataOutPorts")
        if op.exec_in_ports is not None:
            if not isinstance(spec, F8OperatorSpec):
                raise ValueError("execInPorts can only be set on operator nodes")
            _validate_port_edit_policy(spec, collection="execInPorts")
        if op.exec_out_ports is not None:
            if not isinstance(spec, F8OperatorSpec):
                raise ValueError("execOutPorts can only be set on operator nodes")
            _validate_port_edit_policy(spec, collection="execOutPorts")
        updated_spec = self._updated_ports_spec_for_op(node, op)
        _validate_required_data_ports_kept(before=spec, after=updated_spec, collection="dataInPorts")
        _validate_required_data_ports_kept(before=spec, after=updated_spec, collection="dataOutPorts")
        coerce_json_value(dump_json(updated_spec, mode="json", by_alias=True))

    def _apply_node_ports_op(self, op: SetNodePortsOp, *, push_undo: bool) -> None:
        node = self._require_node(op.node_id)
        before_spec = _require_node_spec(node, op.node_id)
        updated_spec = self._updated_ports_spec_for_op(node, op)
        _set_node_spec_value(node, before=before_spec, after=updated_spec, push_undo=push_undo)

    def _validate_node_state_fields_op(self, op: SetNodeStateFieldsOp) -> None:
        node = self._require_node(op.node_id)
        spec = _node_spec(node)
        if spec is None:
            raise ValueError(f"node {op.node_id} has no editable spec")
        _validate_state_field_edit_policy(spec)
        _decode_state_field_specs(op.state_fields, label="stateFields")
        updated_spec = self._updated_state_fields_spec_for_op(node, op)
        _validate_required_state_fields_kept(before=spec, after=updated_spec)
        coerce_json_value(dump_json(updated_spec, mode="json", by_alias=True))

    def _apply_node_state_fields_op(self, op: SetNodeStateFieldsOp, *, push_undo: bool) -> None:
        node = self._require_node(op.node_id)
        before_spec = _require_node_spec(node, op.node_id)
        updated_spec = self._updated_state_fields_spec_for_op(node, op)
        _set_node_spec_value(node, before=before_spec, after=updated_spec, push_undo=push_undo)

    def _updated_ports_spec_for_op(self, node: Any, op: SetNodePortsOp) -> F8OperatorSpec | F8ServiceSpec:
        spec = _spec_copy_for_edit(node, node_id=op.node_id)
        data_in_ports = (
            list(spec.dataInPorts or [])
            if op.data_in_ports is None
            else _decode_data_port_specs(op.data_in_ports, label="dataInPorts")
        )
        data_out_ports = (
            list(spec.dataOutPorts or [])
            if op.data_out_ports is None
            else _decode_data_port_specs(op.data_out_ports, label="dataOutPorts")
        )
        if isinstance(spec, F8OperatorSpec):
            exec_in_ports = list(spec.execInPorts or []) if op.exec_in_ports is None else list(op.exec_in_ports)
            exec_out_ports = list(spec.execOutPorts or []) if op.exec_out_ports is None else list(op.exec_out_ports)
            return set_ports(
                spec,
                data_in=data_in_ports,
                data_out=data_out_ports,
                exec_in=exec_in_ports,
                exec_out=exec_out_ports,
            )
        return set_ports(spec, data_in=data_in_ports, data_out=data_out_ports)

    def _updated_state_fields_spec_for_op(
        self,
        node: Any,
        op: SetNodeStateFieldsOp,
    ) -> F8OperatorSpec | F8ServiceSpec:
        spec = _spec_copy_for_edit(node, node_id=op.node_id)
        return set_state_fields(spec, state_fields=_decode_state_field_specs(op.state_fields, label="stateFields"))

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


def _node_snapshot_to_dict(snapshot: GraphNodeSnapshot) -> dict[str, Any]:
    return asdict(snapshot)


def _node_search_text(snapshot: GraphNodeSnapshot) -> str:
    return " ".join(
        [
            snapshot.node_id,
            snapshot.node_type,
            snapshot.name,
            snapshot.kind,
            snapshot.service_class,
            snapshot.operator_class,
        ]
    ).lower()


def _node_runtime_binding(node: Any) -> dict[str, Any]:
    spec = _node_spec(node)
    node_id = _node_id(node)
    if isinstance(spec, F8ServiceSpec):
        return {"nodeId": node_id, "serviceId": node_id, "runtimeKind": "service"}
    if isinstance(spec, F8OperatorSpec):
        if str(spec.serviceClass or "") == STUDIO_SERVICE_CLASS:
            return {"nodeId": node_id, "serviceId": STUDIO_SERVICE_ID, "runtimeKind": "studio-operator"}
        return {"nodeId": node_id, "serviceId": _node_service_id(node), "runtimeKind": "operator"}
    return {"nodeId": node_id, "serviceId": "", "runtimeKind": ""}


def _node_service_id(node: Any) -> str:
    try:
        return str(node.svcId or "").strip()
    except _GRAPH_READ_ERRORS:
        return ""


def _node_state_values(node: Any) -> dict[str, Any]:
    spec = _node_spec(node)
    if spec is None:
        return {}
    out: dict[str, Any] = {}
    for field in list(spec.stateFields or []):
        name = str(field.name or "").strip()
        if not name:
            continue
        try:
            model = node.model
            if name not in model.properties and name not in model.custom_properties:
                continue
            out[name] = _json_detail_value(model.get_property(name))
        except _GRAPH_READ_ERRORS:
            continue
    binding = _node_runtime_binding(node)
    service_id = str(binding.get("serviceId") or "").strip()
    if service_id:
        out.setdefault(_RUNTIME_BINDING_STATE_FIELD, service_id)
    return out


def _node_properties_payload(node: Any) -> dict[str, Any]:
    try:
        model = node.model
        registered = _json_detail_value(dict(model.properties or {}))
        custom = _json_detail_value(dict(model.custom_properties or {}))
    except _GRAPH_READ_ERRORS:
        return {"registered": {}, "custom": {}}
    return {
        "registered": registered if isinstance(registered, dict) else {},
        "custom": custom if isinstance(custom, dict) else {},
    }


def _node_ui_payload(node: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    try:
        payload["overrides"] = _json_detail_value(node.ui_overrides())
    except _GRAPH_READ_ERRORS:
        payload["overrides"] = {}
    try:
        payload["state"] = _json_detail_value(node.ui_state())
    except _GRAPH_READ_ERRORS:
        payload["state"] = {}
    return payload


def _json_detail_value(value: Any) -> Any:
    return _truncate_json_value(coerce_json_value(value), max_text=_MAX_NODE_DETAIL_TEXT)


def _truncate_json_value(value: Any, *, max_text: int) -> Any:
    if isinstance(value, str):
        if len(value) <= max_text:
            return value
        return value[:max_text] + f"... <truncated {len(value) - max_text} chars>"
    if isinstance(value, list):
        return [_truncate_json_value(item, max_text=max_text) for item in value]
    if isinstance(value, dict):
        return {str(key): _truncate_json_value(item, max_text=max_text) for key, item in value.items()}
    return value


def _spec_to_json(spec: F8OperatorSpec | F8ServiceSpec | None) -> dict[str, Any]:
    if spec is None:
        return {}
    payload = dump_json(spec, mode="json", by_alias=True)
    if isinstance(payload, dict):
        return payload
    return {}


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


def _decode_data_port_specs(raw_ports: tuple[Any, ...], *, label: str) -> list[F8DataPortSpec]:
    out: list[F8DataPortSpec] = []
    seen: set[str] = set()
    for index, raw_port in enumerate(raw_ports):
        if not isinstance(raw_port, dict):
            raise ValueError(f"{label} item #{index} must be an object")
        try:
            port = validate_as(F8DataPortSpec, raw_port)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} item #{index} is not a valid data port spec: {exc}") from exc
        name = str(port.name or "").strip()
        if not name:
            raise ValueError(f"{label} item #{index} requires a non-empty name")
        if name in seen:
            raise ValueError(f"{label} contains duplicate port name: {name}")
        seen.add(name)
        out.append(port)
    return out


def _decode_state_field_specs(raw_fields: tuple[Any, ...], *, label: str) -> list[F8StateSpec]:
    out: list[F8StateSpec] = []
    seen: set[str] = set()
    for index, raw_field in enumerate(raw_fields):
        if not isinstance(raw_field, dict):
            raise ValueError(f"{label} item #{index} must be an object")
        try:
            field = validate_as(F8StateSpec, raw_field)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} item #{index} is not a valid state field spec: {exc}") from exc
        name = str(field.name or "").strip()
        if not name:
            raise ValueError(f"{label} item #{index} requires a non-empty name")
        if name in seen:
            raise ValueError(f"{label} contains duplicate field name: {name}")
        seen.add(name)
        out.append(field)
    return out


def _validate_port_edit_policy(
    spec: F8OperatorSpec | F8ServiceSpec,
    *,
    collection: str,
) -> None:
    if collection == "dataInPorts":
        can_change = bool(
            can_add(spec, "dataInPorts") and can_delete(spec, "dataInPorts") and can_edit_existing(spec, "dataInPorts")
        )
    elif collection == "dataOutPorts":
        can_change = bool(
            can_add(spec, "dataOutPorts")
            and can_delete(spec, "dataOutPorts")
            and can_edit_existing(spec, "dataOutPorts")
        )
    elif collection == "execInPorts":
        can_change = bool(
            can_add(spec, "execInPorts") and can_delete(spec, "execInPorts") and can_edit_existing(spec, "execInPorts")
        )
    elif collection == "execOutPorts":
        can_change = bool(
            can_add(spec, "execOutPorts")
            and can_delete(spec, "execOutPorts")
            and can_edit_existing(spec, "execOutPorts")
        )
    else:
        raise ValueError(f"unsupported port collection: {collection}")
    if not can_change:
        raise ValueError(f"node spec does not allow editing {collection}")


def _validate_state_field_edit_policy(spec: F8OperatorSpec | F8ServiceSpec) -> None:
    can_change = bool(
        can_add(spec, "stateFields") and can_delete(spec, "stateFields") and can_edit_existing(spec, "stateFields")
    )
    if not can_change:
        raise ValueError("node spec does not allow editing stateFields")


def _validate_required_data_ports_kept(
    *,
    before: F8OperatorSpec | F8ServiceSpec,
    after: F8OperatorSpec | F8ServiceSpec,
    collection: str,
) -> None:
    before_ports = list(before.dataInPorts or []) if collection == "dataInPorts" else list(before.dataOutPorts or [])
    after_ports = list(after.dataInPorts or []) if collection == "dataInPorts" else list(after.dataOutPorts or [])
    after_names = {str(port.name or "").strip() for port in after_ports}
    for port in before_ports:
        name = str(port.name or "").strip()
        if name and bool(port.required) and name not in after_names:
            raise ValueError(f"required {collection} port cannot be removed: {name}")


def _validate_required_state_fields_kept(
    *,
    before: F8OperatorSpec | F8ServiceSpec,
    after: F8OperatorSpec | F8ServiceSpec,
) -> None:
    after_names = {str(field.name or "").strip() for field in list(after.stateFields or [])}
    for field in list(before.stateFields or []):
        name = str(field.name or "").strip()
        if name and bool(field.required) and name not in after_names:
            raise ValueError(f"required stateFields field cannot be removed: {name}")


def _copy_node_spec(spec: F8OperatorSpec | F8ServiceSpec) -> F8OperatorSpec | F8ServiceSpec:
    payload = dump_json(spec, mode="json", by_alias=True)
    if not isinstance(payload, dict):
        raise TypeError("node spec JSON payload must be an object")
    if isinstance(spec, F8OperatorSpec):
        return validate_as(F8OperatorSpec, payload)
    return validate_as(F8ServiceSpec, payload)


def _require_node_spec(node: Any, node_id: str) -> F8OperatorSpec | F8ServiceSpec:
    spec = _node_spec(node)
    if spec is None:
        raise ValueError(f"node {node_id} has no editable spec")
    return spec


def _spec_copy_for_edit(node: Any, *, node_id: str) -> F8OperatorSpec | F8ServiceSpec:
    return _copy_node_spec(_require_node_spec(node, node_id))


def _apply_node_spec(node: Any, spec: F8OperatorSpec | F8ServiceSpec) -> None:
    try:
        node.set_spec(spec, rebuild=True)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        logger.exception("failed to apply node spec nodeId=%s", _node_id(node))
        raise


def _copy_node_custom_properties(node: Any) -> dict[str, Any]:
    try:
        return copy.deepcopy(dict(node.model.custom_properties or {}))
    except _GRAPH_READ_ERRORS:
        logger.exception("failed to copy node custom properties nodeId=%s", _node_id(node))
        raise


def _restore_node_custom_properties(node: Any, custom_properties: dict[str, Any]) -> None:
    try:
        model_custom_properties = node.model.custom_properties
        model_custom_properties.clear()
        model_custom_properties.update(copy.deepcopy(custom_properties))
    except (AttributeError, RuntimeError, TypeError, ValueError):
        logger.exception("failed to restore node custom properties nodeId=%s", _node_id(node))
        raise
    try:
        node.view.draw_node()
    except (AttributeError, RuntimeError, TypeError, ValueError):
        logger.debug("failed to redraw node after custom property restore nodeId=%s", _node_id(node), exc_info=True)


def _set_node_spec_value(
    node: Any,
    *,
    before: F8OperatorSpec | F8ServiceSpec,
    after: F8OperatorSpec | F8ServiceSpec,
    push_undo: bool,
) -> None:
    graph = node.graph
    if bool(push_undo) and graph is not None:
        graph.undo_stack().push(
            _NodeSpecChangeCommand(
                node=node,
                before=before,
                after=after,
            )
        )
        return
    _apply_node_spec(node, _copy_node_spec(after))


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
        _set_node_state_value(node, "svcId", normalized_node_id, push_undo=False)


def _set_node_state_value(node: Any, field: str, value: Any, *, push_undo: bool) -> None:
    field_name = str(field or "").strip()
    coerced_value = coerce_json_value(value)
    if _is_runtime_binding_state_field(field_name):
        _set_node_runtime_binding_service_id(node, coerced_value, push_undo=push_undo)
        return
    node.set_property(field_name, coerced_value, push_undo=push_undo)


def _is_runtime_binding_state_field(field_name: str) -> bool:
    return str(field_name or "").strip() == _RUNTIME_BINDING_STATE_FIELD


def _set_node_runtime_binding_service_id(node: Any, value: Any, *, push_undo: bool) -> None:
    service_id = "" if value is None else str(value).strip()
    if _node_service_id(node) == service_id:
        _sync_operator_container_reference_for_service_id(node)
        return
    graph = node.graph
    if bool(push_undo) and graph is not None:
        graph.undo_stack().push(_RuntimeServiceBindingChangeCommand(node, service_id))
        return
    _apply_runtime_binding_service_id(node, service_id)
    _sync_operator_container_reference_for_service_id(node)


def _apply_runtime_binding_service_id(node: Any, service_id: str) -> None:
    normalized_service_id = str(service_id or "").strip()
    node.svcId = normalized_service_id or None
    model = node.model
    if _RUNTIME_BINDING_STATE_FIELD in model.properties or _RUNTIME_BINDING_STATE_FIELD in model.custom_properties:
        node.set_property(_RUNTIME_BINDING_STATE_FIELD, normalized_service_id, push_undo=False)


def _operator_current_container(operator: F8StudioOperatorBaseNode) -> F8StudioContainerBaseNode | None:
    container_item = operator.view._container_item
    if container_item is None:
        return None
    container_id = str(container_item.id or "").strip()
    if not container_id:
        return None
    graph = operator.graph
    if graph is None:
        return None
    container = graph.get_node_by_id(container_id)
    if _is_studio_container_node(container):
        return container
    return None


def _sync_operator_container_reference_for_service_id(node: Any) -> None:
    if not _is_studio_operator_node(node):
        return
    graph = node.graph
    service_id = _node_service_id(node)
    target_container = graph.get_node_by_id(service_id) if graph is not None and service_id else None
    if _is_studio_container_node(target_container) and _operator_container_service_class_matches(
        operator=node,
        container=target_container,
    ):
        _sync_operator_container_reference(operator=node, container=target_container)
        return
    current_container = _operator_current_container(node)
    if current_container is not None:
        current_container.remove_child(node)


def _sync_operator_container_reference(
    *,
    operator: F8StudioOperatorBaseNode,
    container: F8StudioContainerBaseNode,
) -> None:
    current_container = _operator_current_container(operator)
    if current_container is not None and current_container is not container:
        current_container.remove_child(operator)

    child_nodes = container.child_nodes()
    if operator not in child_nodes:
        container.add_child(operator)
        return
    if operator.view not in container.view._child_views:
        container.view.add_child(operator.view)


def _operator_container_service_class_matches(
    *,
    operator: F8StudioOperatorBaseNode,
    container: F8StudioContainerBaseNode,
) -> bool:
    operator_spec = _node_spec(operator)
    container_spec = _node_spec(container)
    if not isinstance(operator_spec, F8OperatorSpec) or not isinstance(container_spec, F8ServiceSpec):
        return False
    return str(operator_spec.serviceClass or "").strip() == str(container_spec.serviceClass or "").strip()


def _node_scene_rect(node: Any) -> QtCore.QRectF:
    view = node.view
    if view.scene() is not None:
        return QtCore.QRectF(view.sceneBoundingRect())
    pos = node.pos()
    bounds = view.boundingRect()
    return QtCore.QRectF(float(pos[0]), float(pos[1]), float(bounds.width()), float(bounds.height()))


def _container_minimum_size(container: F8StudioContainerBaseNode) -> tuple[float, float]:
    minimum_size = container.view.minimum_size
    return (float(minimum_size[0]), float(minimum_size[1]))


def _expanded_container_rect(
    *,
    container_rect: QtCore.QRectF,
    operator_rect: QtCore.QRectF,
) -> QtCore.QRectF:
    padded_operator_rect = QtCore.QRectF(operator_rect)
    padded_operator_rect.adjust(
        -_AUTO_CONTAINER_PADDING,
        -_AUTO_CONTAINER_PADDING,
        _AUTO_CONTAINER_PADDING,
        _AUTO_CONTAINER_PADDING,
    )
    left = min(float(container_rect.left()), float(padded_operator_rect.left()))
    top = min(float(container_rect.top()), float(padded_operator_rect.top()))
    right = max(_rect_right(container_rect), _rect_right(padded_operator_rect))
    bottom = max(_rect_bottom(container_rect), _rect_bottom(padded_operator_rect))
    return QtCore.QRectF(left, top, right - left, bottom - top)


def _container_geometry_changed(before: QtCore.QRectF, after: QtCore.QRectF) -> bool:
    return (
        abs(float(before.x()) - float(after.x())) > _AUTO_CONTAINER_GEOMETRY_EPSILON
        or abs(float(before.y()) - float(after.y())) > _AUTO_CONTAINER_GEOMETRY_EPSILON
        or abs(float(before.width()) - float(after.width())) > _AUTO_CONTAINER_GEOMETRY_EPSILON
        or abs(float(before.height()) - float(after.height())) > _AUTO_CONTAINER_GEOMETRY_EPSILON
    )


def _rect_right(rect: QtCore.QRectF) -> float:
    return float(rect.x()) + float(rect.width())


def _rect_bottom(rect: QtCore.QRectF) -> float:
    return float(rect.y()) + float(rect.height())


def _expand_container_to_cover_operator(
    *,
    container: F8StudioContainerBaseNode,
    operator: F8StudioOperatorBaseNode,
    push_undo: bool,
) -> bool:
    before_rect = _node_scene_rect(container)
    desired_rect = _expanded_container_rect(
        container_rect=before_rect,
        operator_rect=_node_scene_rect(operator),
    )
    minimum_width, minimum_height = _container_minimum_size(container)
    desired_rect.setWidth(max(float(desired_rect.width()), minimum_width))
    desired_rect.setHeight(max(float(desired_rect.height()), minimum_height))
    if not _container_geometry_changed(before_rect, desired_rect):
        return False

    graph = container.graph
    if bool(push_undo) and graph is not None:
        graph.undo_stack().push(
            _ContainerGeometryChangeCommand(
                container=container,
                before=before_rect,
                after=desired_rect,
            )
        )
        return True
    _apply_container_geometry_without_child_translation(container, desired_rect)
    return True


def _apply_container_geometry_without_child_translation(
    container: F8StudioContainerBaseNode,
    rect: QtCore.QRectF,
) -> None:
    child_nodes = list(container._child_nodes)
    child_views = list(container.view._child_views)
    container.view._child_views = []
    try:
        container._apply_backdrop_size(
            width=float(rect.width()),
            height=float(rect.height()),
            pos_x=float(rect.x()),
            pos_y=float(rect.y()),
            push_undo=False,
        )
    finally:
        container._child_nodes = child_nodes
        container.view._child_views = child_views
        for child_view in child_views:
            child_view._container_item = container.view


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
        elif isinstance(op, SetNodePortsOp):
            out.add(op.node_id)
        elif isinstance(op, SetNodeStateFieldsOp):
            out.add(op.node_id)
        elif isinstance(op, MoveNodeOp):
            out.add(op.node_id)
        elif isinstance(op, SetUiOverrideOp):
            out.add(op.node_id)
    return out


def _session_revision(session_payload: dict[str, Any]) -> int:
    revision_payload = _session_revision_payload(session_payload)
    encoded = json.dumps(revision_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    digest = hashlib.blake2b(encoded, digest_size=8).digest()
    return int.from_bytes(digest, byteorder="big", signed=False)


def _session_revision_payload(session_payload: dict[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(session_payload)
    layout = payload.get("layout")
    if not isinstance(layout, dict):
        return payload
    nodes = layout.get("nodes")
    if isinstance(nodes, dict):
        for raw_node in nodes.values():
            if isinstance(raw_node, dict):
                raw_node.pop("selected", None)
    return payload


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
