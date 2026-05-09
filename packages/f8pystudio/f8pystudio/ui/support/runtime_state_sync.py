from __future__ import annotations

from typing import Any

from f8pysdk.specs import F8OperatorSpec, F8ServiceSpec


class RuntimeStateSyncController:
    def __init__(
        self,
        *,
        studio_graph: Any,
        property_editor: Any,
        bridge: Any,
        studio_service_class: str,
    ) -> None:
        self._studio_graph = studio_graph
        self._property_editor = property_editor
        self._bridge = bridge
        self._studio_service_class = str(studio_service_class or "")
        self._applying_runtime_state = False

    def on_runtime_state_updated(self, service_id: str, node_id: str, field: str, value: Any, ts_ms: Any) -> None:
        _ = service_id
        _ = ts_ms
        try:
            node = self._studio_graph.get_node_by_id(str(node_id))
        except Exception:
            node = None
        if node is None:
            return

        try:
            if field not in node.model.properties and field not in node.model.custom_properties:
                return
        except Exception:
            return

        self._applying_runtime_state = True
        try:
            try:
                node.set_property(field, value, push_undo=False)
            except Exception:
                return
            self._refresh_inline_option_pools(node=node, field=field)
            self._sync_property_widget(node=node, field=field, value=value)
        finally:
            self._applying_runtime_state = False

    def on_ui_property_changed(self, node: Any, name: str, value: Any) -> None:
        if self._applying_runtime_state:
            return

        try:
            spec = node.spec
        except Exception:
            spec = None
        if not isinstance(spec, (F8OperatorSpec, F8ServiceSpec)):
            return

        try:
            state_names = {str(s.name or "") for s in (spec.stateFields or [])}
        except Exception:
            state_names = set()
        if str(name) not in state_names:
            return

        try:
            node_id = str(node.id or "")
        except Exception:
            node_id = ""
        if not node_id:
            return

        service_class = str(spec.serviceClass or "")
        if service_class == self._studio_service_class:
            self._bridge.set_local_state(node_id, str(name), value)
            return

        if isinstance(spec, F8ServiceSpec):
            service_id = node_id
        else:
            try:
                service_id = str(node.svcId or "")
            except Exception:
                service_id = ""
        if not service_id:
            return

        self._bridge.set_remote_state(service_id, node_id, str(name), value)

    def _sync_property_widget(self, *, node: Any, field: str, value: Any) -> None:
        try:
            node_id = str(node.id or "")
            editor = self._property_editor.property_editor_for_node_id(node_id)
            widget = editor.get_widget(field) if editor is not None else None
            if widget is None:
                return
            try:
                widget.blockSignals(True)
            except (AttributeError, RuntimeError, TypeError):
                pass
            try:
                widget.set_value(value)
            finally:
                try:
                    widget.blockSignals(False)
                except (AttributeError, RuntimeError, TypeError):
                    pass
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return

    @staticmethod
    def _refresh_inline_option_pools(*, node: Any, field: str) -> None:
        try:
            node.view._refresh_state_inline_option_pools(str(field))
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return
