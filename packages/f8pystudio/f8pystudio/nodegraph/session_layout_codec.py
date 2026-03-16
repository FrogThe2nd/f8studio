from __future__ import annotations

import copy
from f8pysdk.msgspec_codec import copy_model, dump_json, validate_as
import json
import logging
import os
from typing import Any

from qtpy import QtCore
from NodeGraphQt.errors import NodeCreationError

from f8pysdk import F8OperatorSpec, F8ServiceSpec

from .edge_rules import EdgeRuleNodeInfo, layout_node_info, validate_layout_connection
from ..session_migration import extract_layout as _extract_session_layout
from ..session_migration import wrap_layout_for_save as _wrap_layout_for_save

MISSING_SERVICE_NODE_TYPE = "svc.f8.missing.service"
MISSING_OPERATOR_NODE_TYPE = "svc.f8.missing.operator"

logger = logging.getLogger(__name__)


class SessionLayoutCodecMixin:
    @staticmethod
    def _json_default_redacted_value(value_schema: Any) -> Any:
        try:
            schema_json = dump_json(value_schema, mode="json")
        except (AttributeError, TypeError, ValueError):
            schema_json = value_schema
        if not isinstance(schema_json, dict):
            return None

        if "default" in schema_json:
            return copy.deepcopy(schema_json.get("default"))

        schema_type = schema_json.get("type")
        if isinstance(schema_type, list):
            non_null_types = [item for item in schema_type if isinstance(item, str) and item != "null"]
            schema_type = non_null_types[0] if non_null_types else None

        if schema_type == "string":
            return ""
        if schema_type == "array":
            return []
        if schema_type == "object":
            return {}
        if schema_type == "number":
            return 0
        if schema_type == "integer":
            return 0
        if schema_type == "boolean":
            return False
        return None

    @classmethod
    def _redact_publish_session_state_values(cls, layout_data: dict) -> dict:
        nodes = layout_data.get("nodes")
        if not isinstance(nodes, dict):
            return layout_data

        for node_data in nodes.values():
            if not isinstance(node_data, dict):
                continue
            custom = node_data.get("custom")
            raw_spec = node_data.get("f8_spec")
            if not isinstance(custom, dict) or not isinstance(raw_spec, dict):
                continue

            raw_state_fields = raw_spec.get("stateFields")
            if not isinstance(raw_state_fields, list) or not raw_state_fields:
                continue

            for raw_field in raw_state_fields:
                if not isinstance(raw_field, dict):
                    continue
                if not bool(raw_field.get("redactOnPublish")):
                    continue
                field_name = str(raw_field.get("name") or "").strip()
                if not field_name or field_name not in custom:
                    continue
                custom[field_name] = cls._json_default_redacted_value(raw_field.get("valueSchema"))

        return layout_data

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
            if isinstance(v, (F8OperatorSpec, F8ServiceSpec)):
                return v
            if isinstance(v, dict):
                try:
                    if "operatorClass" in v:
                        return validate_as(F8OperatorSpec, v)
                    return validate_as(F8ServiceSpec, v)
                except Exception:
                    return None
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
            ui = node_data.get("f8_ui")
            state_ui = None
            if isinstance(ui, dict):
                state_ui = ui.get("stateFields")
            if isinstance(state_ui, dict) and state_ui and state_fields:
                allowed_keys = {"showOnNode", "uiControl", "uiLanguage", "label", "description"}
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
                    except Exception:
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
            if not (isinstance(out_ref, (list, tuple)) and len(out_ref) == 2 and isinstance(in_ref, (list, tuple)) and len(in_ref) == 2):
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
            if isinstance(v, (F8OperatorSpec, F8ServiceSpec)):
                return v
            if isinstance(v, dict):
                try:
                    if "operatorClass" in v:
                        return validate_as(F8OperatorSpec, v)
                    return validate_as(F8ServiceSpec, v)
                except Exception:
                    return None
            return None

        def _enum_str(v: object) -> str | None:
            if v is None:
                return None
            try:
                import enum

                if isinstance(v, enum.Enum):
                    return str(v.value)
                return str(v)
            except Exception:
                return None

        errors: list[str] = []

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
            except Exception:
                template_spec = None
            session_spec_raw = node_data.get("f8_spec")
            if not isinstance(session_spec_raw, dict) or template_spec is None:
                continue

            # Reject when spec kind mismatches the node class.
            session_is_operator = "operatorClass" in session_spec_raw
            template_is_operator = isinstance(template_spec, F8OperatorSpec)
            if session_is_operator != template_is_operator:
                errors.append(
                    f"nodeId={node_id}: session spec kind mismatch for node type {node_type!r} "
                    f"(template={'operator' if template_is_operator else 'service'}, "
                    f"session={'operator' if session_is_operator else 'service'})"
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

            def _maybe_override_bool(key: str) -> None:
                if key in session_spec_raw:
                    merged[key] = session_spec_raw.get(key)

            def _maybe_override_list(key: str, allow: bool) -> None:
                if not allow:
                    # warn (non-fatal) if the session attempted to override a non-editable list.
                    if key in session_spec_raw and session_spec_raw.get(key) != merged.get(key):
                        logger.warning(
                            "Ignoring non-editable session override: nodeId=%s key=%s (template wins).",
                            node_id,
                            key,
                        )
                    return
                if key in session_spec_raw:
                    merged[key] = session_spec_raw.get(key)

            if isinstance(template_spec, F8OperatorSpec):
                _maybe_override_bool("editableStateFields")
                _maybe_override_bool("editableExecInPorts")
                _maybe_override_bool("editableExecOutPorts")
                _maybe_override_bool("editableDataInPorts")
                _maybe_override_bool("editableDataOutPorts")

                # Keep user metadata from persisted snapshots/variants.
                if "label" in session_spec_raw:
                    merged["label"] = session_spec_raw.get("label")
                if "description" in session_spec_raw:
                    merged["description"] = session_spec_raw.get("description")
                if "tags" in session_spec_raw:
                    merged["tags"] = session_spec_raw.get("tags")
                _maybe_override_list("stateFields", bool(merged.get("editableStateFields", False)))
                _maybe_override_list("execInPorts", bool(merged.get("editableExecInPorts", False)))
                _maybe_override_list("execOutPorts", bool(merged.get("editableExecOutPorts", False)))
                _maybe_override_list("dataInPorts", bool(merged.get("editableDataInPorts", False)))
                _maybe_override_list("dataOutPorts", bool(merged.get("editableDataOutPorts", False)))
                try:
                    node_data["f8_spec"] = dump_json(validate_as(F8OperatorSpec, merged), mode="json")
                except Exception as e:
                    errors.append(f"nodeId={node_id}: failed to merge operator spec: {e}")
            else:
                _maybe_override_bool("editableStateFields")
                _maybe_override_bool("editableCommands")
                _maybe_override_bool("editableDataInPorts")
                _maybe_override_bool("editableDataOutPorts")

                # Keep user metadata from persisted snapshots/variants.
                if "label" in session_spec_raw:
                    merged["label"] = session_spec_raw.get("label")
                if "description" in session_spec_raw:
                    merged["description"] = session_spec_raw.get("description")
                if "tags" in session_spec_raw:
                    merged["tags"] = session_spec_raw.get("tags")
                _maybe_override_list("stateFields", bool(merged.get("editableStateFields", False)))
                _maybe_override_list("commands", bool(merged.get("editableCommands", False)))
                _maybe_override_list("dataInPorts", bool(merged.get("editableDataInPorts", False)))
                _maybe_override_list("dataOutPorts", bool(merged.get("editableDataOutPorts", False)))
                try:
                    node_data["f8_spec"] = dump_json(validate_as(F8ServiceSpec, merged), mode="json")
                except Exception as e:
                    errors.append(f"nodeId={node_id}: failed to merge service spec: {e}")

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
            "missingRendererFallback",
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
            is_operator = "operatorClass" in spec_payload
            placeholder_type = MISSING_OPERATOR_NODE_TYPE if is_operator else MISSING_SERVICE_NODE_TYPE
            if placeholder_type not in self._node_factory.nodes:
                raise NodeCreationError(f"Missing placeholder node class '{placeholder_type}' is not registered.")

            f8_sys_obj = node_data.get("f8_sys")
            if isinstance(f8_sys_obj, dict):
                f8_sys = dict(f8_sys_obj)
            else:
                f8_sys = {}
            f8_sys["missingLocked"] = True
            f8_sys["missingType"] = raw_type
            f8_sys["missingReason"] = f"unregistered node type '{raw_type}'"
            f8_sys["missingRendererFallback"] = bool(f8_sys.get("missingRendererFallback", False))
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
            logger.warning("Restored %s placeholder node(s) back to original type/spec from session metadata.", restored)
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
        return _wrap_layout_for_save(stripped_layout)

    def serialize_publish_session(self) -> dict:
        payload = self.serialize_session()
        layout_data = payload.get("layout")
        if not isinstance(layout_data, dict):
            return payload
        payload["layout"] = self._redact_publish_session_state_values(copy.deepcopy(layout_data))
        return payload

    def load_session(self, file_path: str) -> None:
        """
        Load a NodeGraphQt session file.

        We temporarily disable studio constraints during deserialization, then
        rebuild container/operator bindings based on geometry.
        """
        file_path = file_path.strip()
        if not os.path.isfile(file_path):
            raise IOError(f"file does not exist: {file_path}")

        self._loading_session = True
        try:
            self.clear_session()
            with open(file_path, encoding="utf-8-sig") as data_file:
                payload = json.load(data_file)
            layout_data = _extract_session_layout(payload)
            self._inject_node_ids(layout_data)
            layout_data = self._restore_missing_session_nodes(layout_data)
            layout_data = self._coerce_missing_session_nodes(layout_data)
            layout_data = self._merge_session_specs(layout_data)
            layout_data = self._strip_port_restore_data(layout_data)
            layout_data = self._strip_unknown_session_custom_properties(layout_data)
            layout_data = self._strip_invalid_connections(layout_data)
            super().deserialize_session(layout_data, clear_session=False, clear_undo_stack=True)
            self._model.session = file_path
            self.session_changed.emit(file_path)
        finally:
            self._loading_session = False
        self._rebind_container_children()
        # Session load restores connections after nodes are created/drawn, which can
        # leave inline state widgets with stale editability until the user forces a refresh.
        # Do a post-load pass to apply the "state-edge => readonly" rule.
        QtCore.QTimer.singleShot(0, self._refresh_all_inline_state_read_only)
