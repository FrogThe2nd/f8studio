from __future__ import annotations

import json
from typing import Any, cast

from f8pysdk.specs import F8StateAccess

from ....nodegraph.node_base import F8StudioBaseNode
from ....nodegraph.state_schema import effective_state_fields as _effective_state_fields
from ....nodegraph.state_schema import state_field_access as _state_field_access
from ...support.node_property_support import get_node_spec, state_input_is_connected
from .common import _set_read_only_widget


_UI_OVERRIDE_LIST_ORDER_KEY = "listOrder"
_UI_OVERRIDE_STATE_FIELDS_KEY = "stateFields"
_UI_OVERRIDE_COMMANDS_KEY = "commands"
_UI_OVERRIDE_DATA_PORTS_KEY = "dataPorts"
_UI_OVERRIDE_SHOW_ON_NODE_KEY = "showOnNode"
_PROPERTY_PANEL_NODE_WRITE_ERRORS = (Exception,)
_PROPERTY_PANEL_EDITOR_REFRESH_ERRORS = (Exception,)
_PROPERTY_PANEL_WIDGET_WRITE_ERRORS = (Exception,)


class NodePropertyPanelGraphSyncMixin:
    @staticmethod
    def _named_override_patch_relevant_to_panel_reload(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        patch: dict[str, Any] = {}
        for raw_name, raw_item_patch in value.items():
            name = str(raw_name)
            if not isinstance(raw_item_patch, dict):
                patch[name] = raw_item_patch
                continue
            item_patch = {
                str(item_key): item_value
                for item_key, item_value in raw_item_patch.items()
                if str(item_key) != _UI_OVERRIDE_SHOW_ON_NODE_KEY
            }
            if item_patch:
                patch[name] = item_patch
        return patch

    @staticmethod
    def _data_port_override_patch_relevant_to_panel_reload(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        patch: dict[str, Any] = {}
        for raw_direction, raw_ports_patch in value.items():
            direction = str(raw_direction)
            if not isinstance(raw_ports_patch, dict):
                patch[direction] = raw_ports_patch
                continue
            ports_patch = NodePropertyPanelGraphSyncMixin._named_override_patch_relevant_to_panel_reload(
                raw_ports_patch
            )
            if ports_patch:
                patch[direction] = ports_patch
        return patch

    @staticmethod
    def _ui_overrides_relevant_to_panel_reload(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        patch: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            if key == _UI_OVERRIDE_LIST_ORDER_KEY:
                continue
            if key == _UI_OVERRIDE_STATE_FIELDS_KEY:
                state_fields = NodePropertyPanelGraphSyncMixin._named_override_patch_relevant_to_panel_reload(item)
                if state_fields:
                    patch[key] = state_fields
                continue
            if key == _UI_OVERRIDE_COMMANDS_KEY:
                commands = NodePropertyPanelGraphSyncMixin._named_override_patch_relevant_to_panel_reload(item)
                if commands:
                    patch[key] = commands
                continue
            if key == _UI_OVERRIDE_DATA_PORTS_KEY:
                data_ports = NodePropertyPanelGraphSyncMixin._data_port_override_patch_relevant_to_panel_reload(item)
                if data_ports:
                    patch[key] = data_ports
                continue
            patch[key] = item
        return patch

    @staticmethod
    def _ui_overrides_reload_fingerprint(value: Any) -> str:
        reload_relevant = NodePropertyPanelGraphSyncMixin._ui_overrides_relevant_to_panel_reload(value)
        return json.dumps(reload_relevant, ensure_ascii=False, sort_keys=True, default=str)

    @staticmethod
    def _ui_overrides_reload_fingerprint_from_node(node: F8StudioBaseNode) -> str:
        return NodePropertyPanelGraphSyncMixin._ui_overrides_reload_fingerprint(node.ui_overrides())

    def _on_graph_ports_changed(self, _in_port: Any, _out_port: Any) -> None:
        host = cast(Any, self)
        if host._inspect_mode:
            return
        try:
            in_name = str(_in_port.name() or "")
            out_name = str(_out_port.name() or "")
        except (AttributeError, TypeError):
            return
        if not (
            in_name.startswith("[S]")
            or in_name.endswith("[S]")
            or out_name.startswith("[S]")
            or out_name.endswith("[S]")
        ):
            return
        if host._editor is None or host._node_id is None:
            return
        g = host._node_graph
        if g is None:
            return
        node = g.get_node_by_id(host._node_id)  # type: ignore[attr-defined]
        if node is None:
            return
        spec = get_node_spec(node)
        if spec is None:
            return
        eff_fields = _effective_state_fields(node)
        if not eff_fields:
            eff_fields = list(spec.stateFields or [])

        for f in eff_fields:
            name = str(f.name or "").strip()
            if not name:
                continue
            w = host._editor.get_widget(name)
            if w is None:
                continue
            access = _state_field_access(node, name)
            read_only = access == F8StateAccess.ro or state_input_is_connected(node, name)
            _set_read_only_widget(w, read_only=bool(read_only))

    def _on_editor_property_changed(self, node_id: str, prop_name: str, prop_value: Any) -> None:
        host = cast(Any, self)
        if host._inspect_mode or host._block_signal:
            return
        nid = str(node_id or "").strip()
        if not nid:
            return
        node = host._node_graph.get_node_by_id(nid)
        if node is None:
            return
        try:
            node.set_property(prop_name, prop_value, push_undo=True)
        except _PROPERTY_PANEL_NODE_WRITE_ERRORS:
            host._log_exception("set_property failed nodeId=%s prop=%s", nid, prop_name)

    def _on_editor_property_changing(self, node_id: str, prop_name: str, prop_value: Any) -> None:
        host = cast(Any, self)
        if host._inspect_mode or host._block_signal:
            return
        nid = str(node_id or "").strip()
        if not nid:
            return
        node = host._node_graph.get_node_by_id(nid)
        if node is None:
            return
        try:
            node.set_property(prop_name, prop_value, push_undo=False)
        except _PROPERTY_PANEL_NODE_WRITE_ERRORS:
            host._log_exception("set_property preview failed nodeId=%s prop=%s", nid, prop_name)

    def _on_graph_property_changed(self, node: Any, prop_name: str, prop_value: Any) -> None:
        host = cast(Any, self)
        if host._editor is None or host._node_id is None:
            return
        try:
            if str(node.id or "") != host._node_id:
                return
        except AttributeError:
            return
        prop_key = str(prop_name or "").strip()
        if not prop_key:
            return

        if prop_key == "f8_ui_overrides":
            current_fingerprint = NodePropertyPanelGraphSyncMixin._ui_overrides_reload_fingerprint(prop_value)
            previous_fingerprint = str(host._last_ui_overrides_reload_fingerprint or "")
            host._last_ui_overrides_reload_fingerprint = current_fingerprint
            if current_fingerprint == previous_fingerprint:
                return
            host._editor.reload()
            return

        if prop_key in {"f8_spec", "f8_ui_state"}:
            host._editor.reload()
            return

        try:
            host._editor.refresh_option_pool(prop_key)
        except _PROPERTY_PANEL_EDITOR_REFRESH_ERRORS:
            host._log_exception("refresh_option_pool failed for key=%s", prop_key)

        w = host._editor.get_widget(prop_name)
        if w is None:
            return
        try:
            cur = w.get_value()
        except (AttributeError, RuntimeError, TypeError):
            cur = None
        if cur == prop_value:
            return
        host._block_signal = True
        try:
            w.set_value(prop_value)
        except _PROPERTY_PANEL_WIDGET_WRITE_ERRORS:
            host._log_exception("Failed to update property widget value key=%s", prop_key)
        finally:
            host._block_signal = False

    def _on_graph_layers_changed(self) -> None:
        host = cast(Any, self)
        if host._editor is None:
            return
        host._editor.reload()
