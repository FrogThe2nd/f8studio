from __future__ import annotations

import copy
import enum
from f8pysdk.codec import copy_model, dump_json, validate_as
import json
import logging
import os
from typing import Any, Callable

import msgspec

from qtpy import QtCore, QtWidgets
from NodeGraphQt.base.commands import PortConnectedCmd
from NodeGraphQt.errors import NodeCreationError

from f8pysdk.specs import F8OperatorSpec, F8ServiceSpec
from f8pysdk.command import command_input_port_name, command_output_port_name
from f8pysdk.specs import (
    coerce_spec_payload,
    spec_kind_from_spec,
)
from f8pysdk.specs import (
    can_add as _policy_can_add,
    can_delete as _policy_can_delete,
    can_edit_existing as _policy_can_edit_existing,
)

from .edge_rules import EdgeRuleNodeInfo, layout_node_info, validate_layout_connection
from .layers import augment_layer_defs_for_layout_nodes, layer_defs_to_json, layout_layer_defs_from_layout
from .viewer import F8StudioNodeViewer
from f8pystudio.nodegraph.session_payload_sanitizer import sanitize_session_layout_for_persistence
from f8pystudio.nodegraph.session_payload_sanitizer import strip_runtime_only_state_fields_from_layout
from f8pystudio.nodegraph.session_schema import extract_layout as _extract_session_layout
from f8pystudio.nodegraph.session_schema import wrap_layout_for_save as _wrap_layout_for_save

MISSING_SERVICE_NODE_TYPE = "svc.f8.missing.service"
MISSING_OPERATOR_NODE_TYPE = "svc.f8.missing.operator"

logger = logging.getLogger(__name__)

_SESSION_NODE_WIDGET_ERRORS = (AttributeError, RuntimeError, TypeError)
_SESSION_SPEC_PATCH_ERRORS = (TypeError, ValueError, msgspec.MsgspecError)
_SESSION_SPEC_STRINGIFY_ERRORS = (RuntimeError, TypeError, ValueError)
_SESSION_TEMPLATE_SPEC_ERRORS = (AttributeError, TypeError, ValueError, msgspec.MsgspecError)
_SESSION_SPEC_VALIDATE_ERRORS = (TypeError, ValueError, msgspec.MsgspecError)


class SessionLayoutCodecMixin:
    def _emit_session_loaded(self) -> None:
        return None

    def _deserialize_session_fast(self, layout_data: dict) -> None:
        def _convert_last_list_to_set(data_obj: dict[str, Any]) -> None:
            for key, value in data_obj.items():
                if isinstance(value, dict):
                    _convert_last_list_to_set(value)
                elif isinstance(value, list):
                    data_obj[key] = set(value)

        for attr_name, attr_value in layout_data.get("graph", {}).items():
            if attr_name == "layout_direction":
                self.set_layout_direction(attr_value)
            elif attr_name == "acyclic":
                self.set_acyclic(attr_value)
            elif attr_name == "pipe_collision":
                self.set_pipe_collision(attr_value)
            elif attr_name == "pipe_slicing":
                self.set_pipe_slicing(attr_value)
            elif attr_name == "pipe_style":
                self.set_pipe_style(attr_value)
            elif attr_name == "accept_connection_types":
                parsed_value = json.loads(attr_value)
                _convert_last_list_to_set(parsed_value)
                self.model.accept_connection_types = parsed_value
            elif attr_name == "reject_connection_types":
                parsed_value = json.loads(attr_value)
                _convert_last_list_to_set(parsed_value)
                self.model.reject_connection_types = parsed_value

        nodes_by_id: dict[str, Any] = {}
        for node_id, node_data in layout_data.get("nodes", {}).items():
            identifier = node_data["type_"]
            node = self._node_factory.create_node_instance(identifier)
            if node is None:
                continue
            node.NODE_NAME = node_data.get("name", node.NODE_NAME)
            for prop in node.model.properties.keys():
                if prop in node_data:
                    node.model.set_property(prop, node_data[prop])
            custom_data = node_data.get("custom", {})
            if isinstance(custom_data, dict):
                for prop, value in custom_data.items():
                    node.model.set_property(prop, value)
                    try:
                        widgets = node.view.widgets
                    except _SESSION_NODE_WIDGET_ERRORS as exc:
                        logger.debug(
                            "session node widget lookup failed nodeId=%s prop=%s",
                            node_id,
                            prop,
                            exc_info=exc,
                        )
                        continue
                    if isinstance(widgets, dict) and prop in widgets:
                        widgets[prop].set_value(value)
            nodes_by_id[node_id] = node
            self.add_node(
                node,
                node_data.get("pos"),
                selected=False,
                push_undo=False,
                inherite_graph_style=True,
            )
            if node_data.get("port_deletion_allowed", None):
                node.set_ports(
                    {
                        "input_ports": node_data["input_ports"],
                        "output_ports": node_data["output_ports"],
                    }
                )

        for connection in layout_data.get("connections", []):
            in_node_id, input_name = connection.get("in", ("", ""))
            in_node = nodes_by_id.get(in_node_id) or self.get_node_by_id(in_node_id)
            if in_node is None:
                continue
            in_port = in_node.inputs().get(input_name)

            out_node_id, output_name = connection.get("out", ("", ""))
            out_node = nodes_by_id.get(out_node_id) or self.get_node_by_id(out_node_id)
            if out_node is None:
                continue
            out_port = out_node.outputs().get(output_name)

            if in_port is None or out_port is None:
                continue

            allow_connection = (not in_port.model.connected_ports) or in_port.model.multi_connection
            if allow_connection:
                PortConnectedCmd(in_port, out_port, emit_signal=False).redo()
            in_node.on_input_connected(in_port, out_port)

        self.clear_selection()
        self._undo_stack.clear()

    @staticmethod
    def _strip_port_restore_data(layout_data: dict) -> dict:
        """
        NodeGraphQt session format stores `port_deletion_allowed` plus
        `input_ports`/`output_ports` when ports are removable.

        Loading then calls `node.set_ports(...)`, which rebuilds ports without
        our custom styling (color / custom port painter). Studio nodes already
        define their ports from `spec` in `__init__`, so we strip these keys and
        let nodes rebuild themselves via the node factory.
        """
        nodes = layout_data.get("nodes")
        if not isinstance(nodes, dict):
            return layout_data
        for n_data in nodes.values():
            if not isinstance(n_data, dict):
                continue
            n_data.pop("port_deletion_allowed", None)
            n_data.pop("input_ports", None)
            n_data.pop("output_ports", None)
        return layout_data

    @staticmethod
    def _strip_invalid_connections(layout_data: dict) -> dict:
        """
        Remove connections that reference ports not defined by the node spec.

        This prevents NodeGraphQt from creating "dangling" pipes that later crash
        during paint when ports/nodes are missing (eg. when a state field changes
        `showOnNode` and ports are no longer created).
        """
        nodes = layout_data.get("nodes")
        conns = layout_data.get("connections")
        if not isinstance(nodes, dict) or not isinstance(conns, list):
            return layout_data

        def _coerce_spec(v: object) -> F8OperatorSpec | F8ServiceSpec | None:
            if v is None:
                return None
            try:
                return coerce_spec_payload(v)
            except (TypeError, ValueError):
                return None

        port_sets: dict[str, set[str] | None] = {}
        node_info_by_id: dict[str, EdgeRuleNodeInfo | None] = {}
        for node_id, node_data in nodes.items():
            node_id_str = str(node_id)
            if not isinstance(node_data, dict):
                port_sets[node_id_str] = None
                node_info_by_id[node_id_str] = None
                continue
            node_info_by_id[node_id_str] = layout_node_info(node_id_str, node_data)
            spec = _coerce_spec(node_data.get("f8_spec"))
            if spec is None:
                port_sets[node_id_str] = None
                continue

            # Apply UI overrides (eg. showOnNode) so we can strip connections
            # referencing ports that will not be created.
            state_fields = list(spec.stateFields or [])
            ui = node_data.get("f8_ui_overrides")
            state_ui = None
            if isinstance(ui, dict):
                state_ui = ui.get("stateFields")
            if isinstance(state_ui, dict) and state_ui and state_fields:
                allowed_keys = {"showOnNode", "uiControl", "label", "description"}
                patched = []
                for f in state_fields:
                    name = str(f.name or "").strip()
                    ov = state_ui.get(name) if name else None
                    if not isinstance(ov, dict) or not ov:
                        patched.append(f)
                        continue
                    patch = {k: ov.get(k) for k in allowed_keys if k in ov}
                    try:
                        patched.append(copy_model(f, update=patch))
                    except _SESSION_SPEC_PATCH_ERRORS as exc:
                        logger.debug(
                            "failed to apply session state UI override field=%s",
                            name,
                            exc_info=exc,
                        )
                        patched.append(f)
                state_fields = patched

            ports: set[str] = set()
            if isinstance(spec, F8OperatorSpec):
                for p in list(spec.execInPorts or []):
                    ports.add(f"[E]{p}")
                for p in list(spec.execOutPorts or []):
                    ports.add(f"{p}[E]")
            for p in list(spec.dataInPorts or []):
                try:
                    ports.add(f"[D]{p.name}")
                except (AttributeError, TypeError):
                    continue
            for p in list(spec.dataOutPorts or []):
                try:
                    ports.add(f"{p.name}[D]")
                except (AttributeError, TypeError):
                    continue
            for s in state_fields:
                try:
                    if not bool(s.showOnNode):
                        continue
                    name = str(s.name or "").strip()
                    if not name:
                        continue
                    ports.add(f"[S]{name}")
                    ports.add(f"{name}[S]")
                except (AttributeError, TypeError):
                    continue
            if isinstance(spec, F8ServiceSpec):
                for command in list(spec.commands or []):
                    try:
                        command_name = str(command.name or "").strip()
                    except (AttributeError, TypeError):
                        command_name = ""
                    if not command_name:
                        continue
                    ports.add(command_input_port_name(command_name))
                    ports.add(command_output_port_name(command_name))
            port_sets[node_id_str] = ports

        kept: list[dict[str, Any]] = []
        dropped = 0
        rule_dropped = 0
        for c in conns:
            if not isinstance(c, dict):
                dropped += 1
                continue
            out_ref = c.get("out")
            in_ref = c.get("in")
            if not (
                isinstance(out_ref, (list, tuple))
                and len(out_ref) == 2
                and isinstance(in_ref, (list, tuple))
                and len(in_ref) == 2
            ):
                dropped += 1
                continue
            out_nid, out_port = str(out_ref[0]), str(out_ref[1])
            in_nid, in_port = str(in_ref[0]), str(in_ref[1])
            if out_nid not in nodes or in_nid not in nodes:
                dropped += 1
                continue
            out_ports = port_sets.get(out_nid)
            in_ports = port_sets.get(in_nid)
            if out_ports is not None and out_port not in out_ports:
                dropped += 1
                continue
            if in_ports is not None and in_port not in in_ports:
                dropped += 1
                continue

            allowed, _reason = validate_layout_connection(
                out_node_id=out_nid,
                out_port_name=out_port,
                in_node_id=in_nid,
                in_port_name=in_port,
                node_info_by_id=node_info_by_id,
            )
            if not allowed:
                dropped += 1
                rule_dropped += 1
                continue
            kept.append(c)

        if dropped:
            logger.warning(
                "Stripped %s invalid session connection(s) (%s rule-violating).",
                dropped,
                rule_dropped,
            )
        layout_data["connections"] = kept
        return layout_data

    def _merge_session_specs(self, layout_data: dict) -> dict:
        """
        Merge session-stored `f8_spec` with the library default spec template.

        - Reject load when identity fields conflict (eg. operatorClass/serviceClass).
        - Allow session to override *editable* lists (eg. stateFields) when the
          template enables editing for that category.
        """
        nodes = layout_data.get("nodes")
        if not isinstance(nodes, dict):
            return layout_data

        def _coerce_spec(v: object) -> F8OperatorSpec | F8ServiceSpec | None:
            if v is None:
                return None
            try:
                return coerce_spec_payload(v)
            except (TypeError, ValueError):
                return None

        def _enum_str(v: object) -> str | None:
            if v is None:
                return None
            try:
                if isinstance(v, enum.Enum):
                    return str(v.value)
                return str(v)
            except _SESSION_SPEC_STRINGIFY_ERRORS as exc:
                logger.debug("failed to stringify session spec enum value=%r", v, exc_info=exc)
                return None

        errors: list[str] = []

        def _policy_flags(
            spec_obj: F8OperatorSpec | F8ServiceSpec,
            collection: str,
        ) -> tuple[bool, bool, bool]:
            return (
                _policy_can_add(spec_obj, collection),  # type: ignore[arg-type]
                _policy_can_delete(spec_obj, collection),  # type: ignore[arg-type]
                _policy_can_edit_existing(spec_obj, collection),  # type: ignore[arg-type]
            )

        def _entry_name(entry: object) -> str:
            if not isinstance(entry, dict):
                return ""
            return str(entry.get("name") or "").strip()

        def _state_field_policy_allows(entry: dict[str, Any], key: str) -> bool:
            edit_policy = entry.get("editPolicy")
            if not isinstance(edit_policy, dict):
                return True
            if key not in edit_policy:
                return True
            return bool(edit_policy.get(key))

        def _state_field_can_delete(entry: dict[str, Any]) -> bool:
            return _state_field_policy_allows(entry, "canRename")

        def _state_field_can_edit_value_schema(entry: dict[str, Any]) -> bool:
            return _state_field_policy_allows(entry, "canEditValueSchema")

        def _state_field_can_edit_access(entry: dict[str, Any]) -> bool:
            return _state_field_policy_allows(entry, "canEditAccess")

        def _state_field_can_edit_required(entry: dict[str, Any]) -> bool:
            return _state_field_policy_allows(entry, "canEditRequired")

        def _merge_state_field_item(base: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
            merged_item = dict(base)
            for key in ("label", "description", "showOnNode", "uiControl"):
                if key in session:
                    merged_item[key] = session.get(key)
            if _state_field_can_edit_access(base) and "access" in session:
                merged_item["access"] = session.get("access")
            if _state_field_can_edit_required(base) and "required" in session:
                merged_item["required"] = session.get("required")
            if _state_field_can_edit_value_schema(base) and "valueSchema" in session:
                merged_item["valueSchema"] = session.get("valueSchema")
            return merged_item

        def _merge_data_port_item(base: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
            merged_item = dict(base)
            for key in ("description", "showOnNode", "valueSchema"):
                if key in session:
                    merged_item[key] = session.get(key)
            return merged_item

        def _merge_command_params(base_params_obj: object, session_params_obj: object) -> list[dict[str, Any]]:
            base_params = [item for item in list(base_params_obj or []) if isinstance(item, dict)]
            session_params = [item for item in list(session_params_obj or []) if isinstance(item, dict)]
            session_by_name = {_entry_name(item): item for item in session_params if _entry_name(item)}
            out: list[dict[str, Any]] = []
            for base_param in base_params:
                base_name = _entry_name(base_param)
                if not base_name:
                    out.append(dict(base_param))
                    continue
                session_param = session_by_name.get(base_name)
                merged_param = dict(base_param)
                if session_param is not None:
                    for key in ("description", "uiControl", "valueSchema"):
                        if key in session_param:
                            merged_param[key] = session_param.get(key)
                out.append(merged_param)
            return out

        def _merge_command_item(base: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
            merged_item = dict(base)
            for key in ("description", "showOnNode"):
                if key in session:
                    merged_item[key] = session.get(key)
            if "params" in session:
                merged_item["params"] = _merge_command_params(base.get("params"), session.get("params"))
            return merged_item

        def _named_item_can_delete_by_required(entry: dict[str, Any]) -> bool:
            return not bool(entry.get("required"))

        def _merge_named_list(
            *,
            base_items_obj: object,
            session_items_obj: object,
            can_add: bool,
            can_delete: bool,
            can_edit_existing: bool,
            merge_item: Any,
            can_delete_item: Callable[[dict[str, Any]], bool] = _named_item_can_delete_by_required,
        ) -> list[dict[str, Any]]:
            base_items = [item for item in list(base_items_obj or []) if isinstance(item, dict)]
            session_items = [item for item in list(session_items_obj or []) if isinstance(item, dict)]
            session_by_name = {_entry_name(item): item for item in session_items if _entry_name(item)}
            base_names: set[str] = set()
            out: list[dict[str, Any]] = []

            for base_item in base_items:
                base_name = _entry_name(base_item)
                if not base_name:
                    out.append(dict(base_item))
                    continue
                base_names.add(base_name)
                session_item = session_by_name.get(base_name)
                if session_item is None:
                    if can_delete and can_delete_item(base_item):
                        continue
                    out.append(dict(base_item))
                    continue
                if can_edit_existing:
                    out.append(merge_item(base_item, session_item))
                else:
                    out.append(dict(base_item))

            if can_add:
                for session_item in session_items:
                    session_name = _entry_name(session_item)
                    if not session_name or session_name in base_names:
                        continue
                    out.append(dict(session_item))

            return out

        def _merge_string_list(
            *,
            base_items_obj: object,
            session_items_obj: object,
            can_add: bool,
            can_delete: bool,
        ) -> list[str]:
            base_items = [str(item) for item in list(base_items_obj or []) if str(item).strip()]
            session_items = [str(item) for item in list(session_items_obj or []) if str(item).strip()]
            if not can_add and not can_delete:
                return base_items
            session_set = set(session_items)
            base_set = set(base_items)
            out: list[str] = []
            for item in base_items:
                if can_delete and item not in session_set:
                    continue
                out.append(item)
            if can_add:
                for item in session_items:
                    if item in base_set:
                        continue
                    out.append(item)
            return out

        for node_id, node_data in nodes.items():
            if not isinstance(node_data, dict):
                continue
            f8_sys = node_data.get("f8_sys")
            if isinstance(f8_sys, dict) and bool(f8_sys.get("missingLocked")):
                continue

            node_type = node_data.get("type_")
            if not isinstance(node_type, str) or not node_type.strip():
                continue

            node_cls = self._node_factory.nodes.get(node_type.strip())
            if node_cls is None:
                continue

            try:
                template_spec = _coerce_spec(node_cls.SPEC_TEMPLATE)  # type: ignore[attr-defined]
            except _SESSION_TEMPLATE_SPEC_ERRORS as exc:
                logger.debug(
                    "failed to coerce template spec nodeId=%s nodeType=%s",
                    node_id,
                    node_type,
                    exc_info=exc,
                )
                template_spec = None
            session_spec_raw = node_data.get("f8_spec")
            if not isinstance(session_spec_raw, dict) or template_spec is None:
                continue

            # Reject when spec kind mismatches the node class.
            try:
                session_spec = coerce_spec_payload(session_spec_raw)
            except (TypeError, ValueError):
                errors.append(f"nodeId={node_id}: invalid session spec payload for node type {node_type!r}")
                continue
            session_kind = spec_kind_from_spec(session_spec)
            template_kind = spec_kind_from_spec(template_spec)
            session_is_operator = session_kind == "operator"
            template_is_operator = template_kind == "operator"
            if session_is_operator != template_is_operator:
                errors.append(
                    f"nodeId={node_id}: session spec kind mismatch for node type {node_type!r} "
                    f"(template={template_kind!r}, session={session_kind!r})"
                )
                continue

            # Identity fields must match.
            if isinstance(template_spec, F8OperatorSpec):
                sess_service_class = str(session_spec_raw.get("serviceClass") or "").strip()
                sess_operator_class = str(session_spec_raw.get("operatorClass") or "").strip()
                if sess_service_class and sess_service_class != str(template_spec.serviceClass):
                    errors.append(
                        f"nodeId={node_id}: serviceClass mismatch (session={sess_service_class!r}, template={template_spec.serviceClass!r})"
                    )
                    continue
                if sess_operator_class and sess_operator_class != str(template_spec.operatorClass):
                    errors.append(
                        f"nodeId={node_id}: operatorClass mismatch (session={sess_operator_class!r}, template={template_spec.operatorClass!r})"
                    )
                    continue
                sess_sv = _enum_str(session_spec_raw.get("schemaVersion"))
                tmpl_sv = _enum_str(template_spec.schemaVersion)
                if sess_sv and tmpl_sv and sess_sv != tmpl_sv:
                    errors.append(
                        f"nodeId={node_id}: schemaVersion mismatch (session={sess_sv!r}, template={tmpl_sv!r})"
                    )
                    continue
            else:
                sess_service_class = str(session_spec_raw.get("serviceClass") or "").strip()
                if sess_service_class and sess_service_class != str(template_spec.serviceClass):
                    errors.append(
                        f"nodeId={node_id}: serviceClass mismatch (session={sess_service_class!r}, template={template_spec.serviceClass!r})"
                    )
                    continue
                sess_sv = _enum_str(session_spec_raw.get("schemaVersion"))
                tmpl_sv = _enum_str(template_spec.schemaVersion)
                if sess_sv and tmpl_sv and sess_sv != tmpl_sv:
                    errors.append(
                        f"nodeId={node_id}: schemaVersion mismatch (session={sess_sv!r}, template={tmpl_sv!r})"
                    )
                    continue

            merged = dump_json(template_spec, mode="json")

            if isinstance(template_spec, F8OperatorSpec):
                # Keep user metadata from persisted snapshots/variants.
                if "label" in session_spec_raw:
                    merged["label"] = session_spec_raw.get("label")
                if "description" in session_spec_raw:
                    merged["description"] = session_spec_raw.get("description")
                if "tags" in session_spec_raw:
                    merged["tags"] = session_spec_raw.get("tags")
                if "editPolicy" in session_spec_raw:
                    merged["editPolicy"] = session_spec_raw.get("editPolicy")
                merged_spec = validate_as(F8OperatorSpec, merged)
                can_add_state, can_delete_state, can_edit_state = _policy_flags(merged_spec, "stateFields")
                can_add_exec_in, can_delete_exec_in, _can_edit_exec_in = _policy_flags(merged_spec, "execInPorts")
                can_add_exec_out, can_delete_exec_out, _can_edit_exec_out = _policy_flags(merged_spec, "execOutPorts")
                can_add_data_in, can_delete_data_in, can_edit_data_in = _policy_flags(merged_spec, "dataInPorts")
                can_add_data_out, can_delete_data_out, can_edit_data_out = _policy_flags(merged_spec, "dataOutPorts")
                merged["stateFields"] = _merge_named_list(
                    base_items_obj=merged.get("stateFields"),
                    session_items_obj=session_spec_raw.get("stateFields"),
                    can_add=can_add_state,
                    can_delete=can_delete_state,
                    can_edit_existing=can_edit_state,
                    merge_item=_merge_state_field_item,
                    can_delete_item=_state_field_can_delete,
                )
                merged["execInPorts"] = _merge_string_list(
                    base_items_obj=merged.get("execInPorts"),
                    session_items_obj=session_spec_raw.get("execInPorts"),
                    can_add=can_add_exec_in,
                    can_delete=can_delete_exec_in,
                )
                merged["execOutPorts"] = _merge_string_list(
                    base_items_obj=merged.get("execOutPorts"),
                    session_items_obj=session_spec_raw.get("execOutPorts"),
                    can_add=can_add_exec_out,
                    can_delete=can_delete_exec_out,
                )
                merged["dataInPorts"] = _merge_named_list(
                    base_items_obj=merged.get("dataInPorts"),
                    session_items_obj=session_spec_raw.get("dataInPorts"),
                    can_add=can_add_data_in,
                    can_delete=can_delete_data_in,
                    can_edit_existing=can_edit_data_in,
                    merge_item=_merge_data_port_item,
                )
                merged["dataOutPorts"] = _merge_named_list(
                    base_items_obj=merged.get("dataOutPorts"),
                    session_items_obj=session_spec_raw.get("dataOutPorts"),
                    can_add=can_add_data_out,
                    can_delete=can_delete_data_out,
                    can_edit_existing=can_edit_data_out,
                    merge_item=_merge_data_port_item,
                )
                try:
                    node_data["f8_spec"] = dump_json(validate_as(F8OperatorSpec, merged), mode="json")
                except _SESSION_SPEC_VALIDATE_ERRORS as exc:
                    errors.append(f"nodeId={node_id}: failed to merge operator spec: {exc}")
            else:
                # Keep user metadata from persisted snapshots/variants.
                if "label" in session_spec_raw:
                    merged["label"] = session_spec_raw.get("label")
                if "description" in session_spec_raw:
                    merged["description"] = session_spec_raw.get("description")
                if "tags" in session_spec_raw:
                    merged["tags"] = session_spec_raw.get("tags")
                if "editPolicy" in session_spec_raw:
                    merged["editPolicy"] = session_spec_raw.get("editPolicy")
                merged_spec = validate_as(F8ServiceSpec, merged)
                can_add_state, can_delete_state, can_edit_state = _policy_flags(merged_spec, "stateFields")
                can_add_commands, can_delete_commands, can_edit_commands = _policy_flags(merged_spec, "commands")
                can_add_data_in, can_delete_data_in, can_edit_data_in = _policy_flags(merged_spec, "dataInPorts")
                can_add_data_out, can_delete_data_out, can_edit_data_out = _policy_flags(merged_spec, "dataOutPorts")
                merged["stateFields"] = _merge_named_list(
                    base_items_obj=merged.get("stateFields"),
                    session_items_obj=session_spec_raw.get("stateFields"),
                    can_add=can_add_state,
                    can_delete=can_delete_state,
                    can_edit_existing=can_edit_state,
                    merge_item=_merge_state_field_item,
                    can_delete_item=_state_field_can_delete,
                )
                merged["commands"] = _merge_named_list(
                    base_items_obj=merged.get("commands"),
                    session_items_obj=session_spec_raw.get("commands"),
                    can_add=can_add_commands,
                    can_delete=can_delete_commands,
                    can_edit_existing=can_edit_commands,
                    merge_item=_merge_command_item,
                )
                merged["dataInPorts"] = _merge_named_list(
                    base_items_obj=merged.get("dataInPorts"),
                    session_items_obj=session_spec_raw.get("dataInPorts"),
                    can_add=can_add_data_in,
                    can_delete=can_delete_data_in,
                    can_edit_existing=can_edit_data_in,
                    merge_item=_merge_data_port_item,
                )
                merged["dataOutPorts"] = _merge_named_list(
                    base_items_obj=merged.get("dataOutPorts"),
                    session_items_obj=session_spec_raw.get("dataOutPorts"),
                    can_add=can_add_data_out,
                    can_delete=can_delete_data_out,
                    can_edit_existing=can_edit_data_out,
                    merge_item=_merge_data_port_item,
                )
                try:
                    node_data["f8_spec"] = dump_json(validate_as(F8ServiceSpec, merged), mode="json")
                except _SESSION_SPEC_VALIDATE_ERRORS as exc:
                    errors.append(f"nodeId={node_id}: failed to merge service spec: {exc}")

        if errors:
            for msg in errors:
                logger.warning("Session spec mismatch: %s", msg)
            preview = "; ".join(errors[:3])
            raise NodeCreationError(
                "Cannot load session due to spec mismatch. "
                f"{preview}. Fix the session file or remove the conflicting nodes."
            )

        return layout_data

    @staticmethod
    def _strip_runtime_only_state_fields_on_load(layout_data: dict) -> dict:
        return strip_runtime_only_state_fields_from_layout(layout_data)

    @staticmethod
    def _inject_node_ids(layout_data: dict) -> None:
        """
        NodeGraphQt stores node ids as keys under `nodes`, but does not include
        them in each node dict. We inject `id` so deserialization restores
        stable ids (instead of the default `0x...`).
        """
        nodes = layout_data.get("nodes")
        if not isinstance(nodes, dict):
            return
        for node_id, node_data in nodes.items():
            if isinstance(node_data, dict) and "id" not in node_data:
                node_data["id"] = node_id

    @staticmethod
    def _coerce_layout_spec(spec_obj: object) -> dict[str, Any] | None:
        if isinstance(spec_obj, dict):
            return spec_obj
        if isinstance(spec_obj, (F8OperatorSpec, F8ServiceSpec)):
            return dump_json(spec_obj, mode="json")
        return None

    @staticmethod
    def _strip_missing_lock_for_save(node_data: dict[str, Any]) -> None:
        f8_sys_obj = node_data.get("f8_sys")
        if not isinstance(f8_sys_obj, dict):
            return
        keys_to_remove = (
            "missingLocked",
            "missingType",
            "missingReason",
            "missingSpec",
            "missingOriginalName",
        )
        for key in keys_to_remove:
            f8_sys_obj.pop(key, None)

    def _coerce_missing_session_nodes(self, layout_data: dict) -> dict:
        """
        Convert unknown session nodes to missing placeholders while preserving ids/spec/connections.
        """
        nodes = layout_data.get("nodes")
        if not isinstance(nodes, dict):
            return layout_data

        converted = 0
        for node_id, node_data in nodes.items():
            if not isinstance(node_data, dict):
                continue

            raw_type = str(node_data.get("type_") or "").strip()
            if not raw_type:
                raise NodeCreationError(f"Cannot load session node '{node_id}': missing `type_`.")
            if raw_type in self._node_factory.nodes:
                continue

            spec_payload = self._coerce_layout_spec(node_data.get("f8_spec"))
            if spec_payload is None:
                raise NodeCreationError(
                    f"Cannot load unknown node type '{raw_type}' (nodeId={node_id}): missing or invalid `f8_spec`."
                )
            spec_kind = spec_kind_from_spec(coerce_spec_payload(spec_payload))
            placeholder_type = MISSING_OPERATOR_NODE_TYPE if spec_kind == "operator" else MISSING_SERVICE_NODE_TYPE
            if placeholder_type not in self._node_factory.nodes:
                raise NodeCreationError(f"Missing placeholder node class '{placeholder_type}' is not registered.")

            f8_sys_obj = node_data.get("f8_sys")
            if isinstance(f8_sys_obj, dict):
                f8_sys = dict(f8_sys_obj)
            else:
                f8_sys = {}
            f8_sys.pop("missingRendererFallback", None)
            f8_sys["missingLocked"] = True
            f8_sys["missingType"] = raw_type
            f8_sys["missingReason"] = f"unregistered node type '{raw_type}'"
            f8_sys["missingSpec"] = dict(spec_payload)
            raw_name = str(node_data.get("name") or "").strip()
            if raw_name and not raw_name.endswith("[Missing]"):
                f8_sys["missingOriginalName"] = raw_name
                node_data["name"] = f"{raw_name} [Missing]"
            node_data["f8_sys"] = f8_sys
            node_data["type_"] = placeholder_type
            node_data["f8_spec"] = spec_payload
            converted += 1

        if converted:
            logger.warning("Recovered %s missing node(s) as placeholders.", converted)
        return layout_data

    def _restore_missing_session_nodes(self, layout_data: dict) -> dict:
        """
        Restore original type/spec for sessions that accidentally persisted placeholder node types.
        """
        nodes = layout_data.get("nodes")
        if not isinstance(nodes, dict):
            return layout_data

        restored = 0
        for _node_id, node_data in nodes.items():
            if not isinstance(node_data, dict):
                continue
            node_type = str(node_data.get("type_") or "").strip()
            if node_type not in {MISSING_OPERATOR_NODE_TYPE, MISSING_SERVICE_NODE_TYPE}:
                continue
            f8_sys = node_data.get("f8_sys")
            if not isinstance(f8_sys, dict):
                continue
            missing_type = str(f8_sys.get("missingType") or "").strip()
            missing_spec = self._coerce_layout_spec(f8_sys.get("missingSpec"))
            if not missing_type or missing_spec is None:
                continue
            node_data["type_"] = missing_type
            node_data["f8_spec"] = missing_spec
            restored += 1
        if restored:
            logger.warning(
                "Restored %s placeholder node(s) back to original type/spec from session metadata.", restored
            )
        return layout_data

    @staticmethod
    def _strip_unknown_session_custom_properties(layout_data: dict) -> dict:
        """
        Drop session custom properties that are not present in the persisted spec.

        NodeGraphQt requires that every key under `custom` is a registered node
        property.
        """
        nodes = layout_data.get("nodes")
        if not isinstance(nodes, dict):
            return layout_data

        for node_id, node_data in nodes.items():
            if not isinstance(node_data, dict):
                continue
            custom = node_data.get("custom")
            if not isinstance(custom, dict) or not custom:
                continue
            raw_spec = node_data.get("f8_spec")
            if not isinstance(raw_spec, dict):
                continue

            allowed: set[str] = set()
            for sf in list(raw_spec.get("stateFields") or []):
                if not isinstance(sf, dict):
                    continue
                name = str(sf.get("name") or "").strip()
                if name:
                    allowed.add(name)

            if not allowed:
                node_data["custom"] = {}
                continue

            kept: dict[str, Any] = {}
            for k, v in custom.items():
                key = str(k)
                if key in allowed:
                    kept[key] = v

            if kept != custom:
                node_data["custom"] = kept

        return layout_data

    def serialize_session(self):
        data = super().serialize_session()
        stripped_layout = self._strip_port_restore_data(data)
        nodes = stripped_layout.get("nodes")
        if isinstance(nodes, dict):
            for _node_id, node_data in nodes.items():
                if not isinstance(node_data, dict):
                    continue
                f8_sys_obj = node_data.get("f8_sys")
                if isinstance(f8_sys_obj, dict) and bool(f8_sys_obj.get("missingLocked")):
                    missing_type = str(f8_sys_obj.get("missingType") or "").strip()
                    missing_spec = self._coerce_layout_spec(f8_sys_obj.get("missingSpec"))
                    if missing_type and missing_spec is not None:
                        node_data["type_"] = missing_type
                        node_data["f8_spec"] = missing_spec
                        missing_original_name = str(f8_sys_obj.get("missingOriginalName") or "").strip()
                        if missing_original_name:
                            node_data["name"] = missing_original_name
                self._strip_missing_lock_for_save(node_data)
        stripped_layout = sanitize_session_layout_for_persistence(
            stripped_layout,
            redact_publish_state_values=False,
        )
        stripped_layout["f8_layers"] = layer_defs_to_json(self.session_layer_defs())
        return _wrap_layout_for_save(stripped_layout)

    def serialize_publish_session(self) -> dict:
        payload = self.serialize_session()
        layout_data = payload.get("layout")
        if not isinstance(layout_data, dict):
            return payload
        payload["layout"] = sanitize_session_layout_for_persistence(
            layout_data,
            redact_publish_state_values=True,
        )
        return payload

    def load_session_payload(self, payload: dict) -> None:
        self._load_session_layout_data(_extract_session_layout(payload), session_label="")

    def load_session(self, file_path: str) -> None:
        """
        Load a NodeGraphQt session file.

        We temporarily disable studio constraints during deserialization, then
        rebuild container/operator bindings based on geometry.
        """
        file_path = file_path.strip()
        if not os.path.isfile(file_path):
            raise IOError(f"file does not exist: {file_path}")

        with open(file_path, encoding="utf-8-sig") as data_file:
            payload = json.load(data_file)
        self._load_session_layout_data(_extract_session_layout(payload), session_label=file_path)

    def _load_session_layout_data(self, layout_data: dict, *, session_label: str) -> None:
        graph_widget = self.widget
        viewer = self.viewer()
        widgets_to_freeze: list[QtWidgets.QWidget] = []
        if isinstance(graph_widget, QtWidgets.QWidget):
            widgets_to_freeze.append(graph_widget)
        if isinstance(viewer, QtWidgets.QWidget) and viewer is not graph_widget:
            widgets_to_freeze.append(viewer)
        for widget in widgets_to_freeze:
            widget.setUpdatesEnabled(False)
        self._loading_session = True
        try:
            self.clear_session()
            layer_defs = augment_layer_defs_for_layout_nodes(
                layout_layer_defs_from_layout(layout_data),
                layout_data.get("nodes"),
            )
            self._inject_node_ids(layout_data)
            layout_data = self._strip_runtime_only_state_fields_on_load(layout_data)
            layout_data = self._restore_missing_session_nodes(layout_data)
            layout_data = self._coerce_missing_session_nodes(layout_data)
            layout_data = self._merge_session_specs(layout_data)
            layout_data = self._strip_runtime_only_state_fields_on_load(layout_data)
            layout_data = self._strip_port_restore_data(layout_data)
            layout_data = self._strip_unknown_session_custom_properties(layout_data)
            layout_data = self._strip_invalid_connections(layout_data)
            deserialize_layout = dict(layout_data)
            deserialize_layout.pop("f8_layers", None)
            self._deserialize_session_fast(deserialize_layout)
            self.set_session_layer_defs(layer_defs, preserve_active=False)
            self._model.session = session_label
            self.session_changed.emit(session_label)
        finally:
            self._loading_session = False
            for widget in reversed(widgets_to_freeze):
                widget.setUpdatesEnabled(True)
                widget.update()
        if bool(self._skip_post_load_container_rebind):
            self._restore_container_children_from_service_ids()
        else:
            self._rebind_container_children()
        # Session load restores connections after nodes are created/drawn, which can
        # leave inline state widgets with stale editability until the user forces a refresh.
        # Do a post-load pass to apply the "state-edge => readonly" rule.
        if not bool(self._skip_post_load_viewer_refresh):
            QtCore.QTimer.singleShot(0, self._refresh_all_inline_state_read_only)
            if isinstance(viewer, F8StudioNodeViewer):
                QtCore.QTimer.singleShot(0, lambda: viewer.refresh_auto_proxy_mode(force=True))
        self._emit_session_loaded()
