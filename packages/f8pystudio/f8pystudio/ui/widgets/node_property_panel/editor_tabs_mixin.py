from __future__ import annotations

import logging
from typing import Any, cast

from qtpy import QtWidgets

from f8pysdk.specs import F8OperatorSpec, F8ServiceSpec, can_add as _policy_can_add

from ....nodegraph.state_schema import schema_type_any as _schema_type
from ...support.node_property_support import get_node_spec
from .common import _wrap_tab_page
from .containers import _F8LabeledStackContainer, _F8StateContainer, _F8StateStackContainer

logger = logging.getLogger(__name__)


class NodePropertyEditorTabsMixin:
    @staticmethod
    def _tab_widget(host: Any) -> QtWidgets.QTabWidget:
        return cast(QtWidgets.QTabWidget, host._F8StudioNodePropEditorWidget__tab)

    @staticmethod
    def _tab_windows(host: Any) -> dict[str, Any]:
        return cast(dict[str, Any], host._F8StudioNodePropEditorWidget__tab_windows)

    @staticmethod
    def _widget_api(widget: QtWidgets.QWidget) -> Any:
        return cast(Any, widget)

    @staticmethod
    def _should_show_commands_tab(spec: F8OperatorSpec | F8ServiceSpec) -> bool:
        command_specs = list(spec.commands or [])
        if command_specs:
            return True
        return _policy_can_add(spec, "commands")

    @staticmethod
    def _adopt_widget_parent(widget: QtWidgets.QWidget, parent: QtWidgets.QWidget) -> QtWidgets.QWidget:
        try:
            current_parent = widget.parentWidget()
        except (AttributeError, RuntimeError, TypeError):
            current_parent = None
        if current_parent is None:
            try:
                widget.setParent(parent)
            except (AttributeError, RuntimeError, TypeError):
                logger.exception("Failed to adopt temporary parent for property widget")
        return widget

    @staticmethod
    def _is_json_state_value(node: Any, prop_name: str) -> bool:
        spec = get_node_spec(node)
        if spec is None:
            return False
        try:
            fields = list(spec.stateFields or [])
        except Exception:
            fields = []
        for field in fields:
            try:
                if str(field.name or "").strip() != prop_name:
                    continue
            except (AttributeError, TypeError):
                continue
            try:
                value_schema = field.valueSchema
            except Exception:
                value_schema = None
            return _schema_type(value_schema) in {"object", "array", "any"}
        return False

    @staticmethod
    def _reorder_tabs(tab_widget: QtWidgets.QTabWidget, preferred: list[str]) -> None:
        if tab_widget.count() <= 1:
            return
        current = [tab_widget.tabText(index) for index in range(tab_widget.count())]
        preferred_present = [tab_name for tab_name in preferred if tab_name in current]
        rest = [tab_name for tab_name in current if tab_name not in preferred_present]
        target = preferred_present + rest
        bar = tab_widget.tabBar()
        for dst, name in enumerate(target):
            for src in range(bar.count()):
                if tab_widget.tabText(src) == name:
                    if src != dst:
                        bar.moveTab(src, dst)
                    break

    def add_widget(self, name, widget, tab="Properties"):
        host = cast(Any, self)
        tab_windows = self._tab_windows(host)
        if tab not in tab_windows:
            tab = "Properties"
        if tab not in tab_windows:
            host.add_tab(tab)
        window = tab_windows[tab]
        window.add_widget(name, widget)
        if not host._inspect_mode:
            widget_api = self._widget_api(widget)
            widget_api.value_changed.connect(host._on_property_changed)
            try:
                widget_api.value_changing.connect(host._on_property_changing)
            except (AttributeError, RuntimeError, TypeError):
                return

    def add_tab(self, name):
        host = cast(Any, self)
        tab_windows = self._tab_windows(host)
        tab_widget = self._tab_widget(host)
        if name in tab_windows:
            raise AssertionError(f"Tab name {name} already taken!")
        if name == "State":
            window = _F8StateStackContainer(host)
        elif name == "Node":
            window = _F8LabeledStackContainer(host)
        else:
            window = _F8StateContainer(host)
        tab_windows[name] = window
        if name == "State":
            assert isinstance(window, _F8StateStackContainer)
            window.edit_state_field_requested.connect(host.open_state_field_editor)
            if not host._inspect_mode:
                window.delete_state_field_requested.connect(host.delete_state_field)
                window.add_state_field_requested.connect(host.add_state_field)
                window.toggle_state_field_show_on_node_requested.connect(host._toggle_state_field_show_on_node)
                window.state_field_order_changed.connect(host._reorder_state_fields)
        tab_widget.addTab(_wrap_tab_page(window), name)
        return window

    def get_tab_widget(self):
        return self._tab_widget(cast(Any, self))

    def get_widget(self, name):
        host = cast(Any, self)
        if name == "name":
            return host.name_wgt
        for prop_win in self._tab_windows(host).values():
            widget = prop_win.get_widget(name)
            if widget:
                return widget

    def get_all_property_widgets(self):
        host = cast(Any, self)
        widgets = [host.name_wgt]
        for prop_win in self._tab_windows(host).values():
            for widget in prop_win.get_all_widgets().values():
                widgets.append(widget)
        return widgets

    def get_port_connection_widget(self):
        return cast(Any, self)._port_connections

    def set_port_lock_widgets_disabled(self, disabled=True):
        _ = disabled
        return
