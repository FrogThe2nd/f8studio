from __future__ import annotations

from typing import Any, cast

from f8pysdk.specs import F8StateAccess

from ....nodegraph.state_schema import effective_state_fields as _effective_state_fields
from ....nodegraph.state_schema import state_field_access as _state_field_access
from ...support.node_property_support import get_node_spec, state_input_is_connected
from .common import _set_read_only_widget


class NodePropertyPanelGraphSyncMixin:
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
        except Exception:
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
        except Exception:
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

        if prop_key in {"f8_spec", "f8_ui_overrides", "f8_ui_state"}:
            host._editor.reload()
            return

        try:
            host._editor.refresh_option_pool(prop_key)
        except Exception:
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
        except Exception:
            host._log_exception("Failed to update property widget value key=%s", prop_key)
        finally:
            host._block_signal = False

    def _on_graph_layers_changed(self) -> None:
        host = cast(Any, self)
        if host._editor is None:
            return
        host._editor.reload()
