from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, cast

from NodeGraphQt.constants import NodePropWidgetEnum
from NodeGraphQt.custom_widgets.properties_bin.node_property_factory import NodePropertyWidgetFactory
from NodeGraphQt.custom_widgets.properties_bin.prop_widgets_base import PropLabel
from qtpy import QtWidgets

from f8pysdk.specs import (
    F8OperatorSpec,
    F8ServiceSpec,
    F8StateAccess,
    can_add as _policy_can_add,
    can_delete_state_field as _policy_can_delete_state_field,
    can_delete as _policy_can_delete,
)

from ....nodegraph.node_base import F8StudioBaseNode
from ....nodegraph.node_graph import F8StudioGraph
from ....nodegraph.node_text_fields import node_text_editor_binding
from ....nodegraph.state_schema import (
    effective_state_fields as _effective_state_fields,
    state_field_access as _state_field_access,
    state_field_ui_control as _state_field_ui_control,
)
from ....ui.support.ui_control import parse_ui_control
from ...components.state_editors import (
    F8BoolSwitchEditor,
    F8CodeButtonEditor as _F8CodeButtonEditor,
    F8InlineCodeEditor as _F8InlineCodeEditor,
    F8JsonValueEditor as _F8JsonValueEditor,
    F8MultiSelectEditor,
    F8OptionComboEditor,
)
from ...support.node_property_support import (
    build_editor_assist_context,
    get_node_spec,
    node_missing_lock_info,
    state_input_is_connected,
)
from ...support.state_panel_controls import build_state_panel_control as _build_state_panel_control
from .commands import _F8SpecCommandEditor
from .common import _apply_read_only_widget, _set_read_only_widget
from .containers import _F8StateStackContainer
from .editor_tabs_mixin import NodePropertyEditorTabsMixin
from .layer_membership_editor import F8LayerMembershipEditor
from .ports import _F8SpecPortEditor

logger = logging.getLogger(__name__)


class NodePropertyEditorBuildMixin(NodePropertyEditorTabsMixin):
    @staticmethod
    def _state_field_names(spec: Any) -> set[str]:
        names: set[str] = set()
        if spec is None:
            return names
        try:
            fields = list(spec.stateFields or [])
        except Exception:
            return names
        for field in fields:
            try:
                name = str(field.name or "").strip()
            except Exception:
                continue
            if name:
                names.add(name)
        return names

    @staticmethod
    def _tab_name_for_property(model: Any, prop_name: str, state_field_names: set[str]) -> str:
        try:
            return str(model.get_tab_name(prop_name) or "Properties")
        except KeyError:
            if prop_name in state_field_names:
                logger.warning("Missing tab metadata for state property '%s'; fallback to State tab.", prop_name)
                return "State"
            logger.warning("Missing tab metadata for property '%s'; fallback to Properties tab.", prop_name)
            return "Properties"

    @staticmethod
    def _widget_type_for_property(model: Any, prop_name: str, state_field_names: set[str]) -> int:
        try:
            value = model.get_widget_type(prop_name)
        except KeyError:
            if prop_name in state_field_names:
                logger.warning(
                    "Missing widget metadata for state property '%s'; fallback to line edit.",
                    prop_name,
                )
            else:
                logger.warning("Missing widget metadata for property '%s'; fallback to line edit.", prop_name)
            return NodePropWidgetEnum.QLINE_EDIT.value
        if value is None:
            return NodePropWidgetEnum.QLINE_EDIT.value
        return int(value)

    @staticmethod
    def _connect_widget_signals(host: Any, widget: QtWidgets.QWidget) -> None:
        widget_api = NodePropertyEditorTabsMixin._widget_api(widget)
        widget_api.value_changed.connect(host._on_property_changed)
        try:
            widget_api.value_changing.connect(host._on_property_changing)
        except (AttributeError, RuntimeError, TypeError):
            return

    @staticmethod
    def _register_option_pool_dependent(host: Any, pool_name: str, widget: Any) -> None:
        host._option_pool_dependents.setdefault(pool_name, []).append(widget)

    def _build_property_widget(
        self,
        *,
        host: Any,
        node: F8StudioBaseNode,
        prop_name: str,
        widget_type: int,
        widget_factory: NodePropertyWidgetFactory,
    ) -> QtWidgets.QWidget:
        widget = _build_state_panel_control(
            node=node,
            prop_name=prop_name,
            widget_type=widget_type,
            widget_factory=widget_factory,
            register_option_pool_dependent=lambda pool, current_widget: self._register_option_pool_dependent(
                host,
                pool,
                current_widget,
            ),
        )
        return self._adopt_widget_parent(widget, host)

    @staticmethod
    def _apply_common_property_metadata(widget: QtWidgets.QWidget, common_prop: dict[str, Any], prop_name: str) -> str | None:
        widget_api = NodePropertyEditorTabsMixin._widget_api(widget)
        tooltip = common_prop.get("tooltip")
        if "items" in common_prop:
            widget_api.set_items(common_prop["items"])
        if "range" in common_prop:
            prop_range = common_prop["range"]
            try:
                widget_api.set_min(prop_range[0])
                widget_api.set_max(prop_range[1])
            except (AttributeError, RuntimeError, TypeError, ValueError):
                try:
                    widget_api.setMinimum(prop_range[0])
                    widget_api.setMaximum(prop_range[1])
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    logger.exception("Failed to apply numeric range for property '%s'", prop_name)
        return str(tooltip) if tooltip is not None else None

    @staticmethod
    def _build_code_editor_widget(
        *,
        host: Any,
        node: F8StudioBaseNode,
        prop_name: str,
    ) -> _F8CodeButtonEditor | None:
        ui_control_raw = _state_field_ui_control(node, prop_name)
        parsed_ui = parse_ui_control(ui_control_raw)
        if parsed_ui.control_name != "code":
            return None
        ui_language = parsed_ui.ui_language or "plaintext"
        widget = _F8CodeButtonEditor(
            host,
            title=f"{node.name()} - {prop_name}",
            language=ui_language,
        )
        widget.set_name(prop_name)
        graph = node.graph
        node_id = str(node.id or "").strip()
        warning_parent = host.window() if host.window() is not None else host
        text_binding = node_text_editor_binding(graph, node_id, prop_name, warning_parent=warning_parent)
        if text_binding is not None:
            widget.set_persisted_value_getter(text_binding.value_getter)
            widget.set_persisted_value_setter(text_binding.value_setter)
            widget.set_persisted_target_exists_provider(text_binding.target_exists)
            widget.set_editor_session_key(text_binding.session_key)
        widget.set_editor_assist_context(
            build_editor_assist_context(
                graph,
                node_id=node_id,
                prop_name=prop_name,
                language=ui_language,
            )
        )
        widget.set_editor_assist_context_provider(
            lambda current_graph=graph, current_node_id=node_id, current_prop=prop_name, current_lang=ui_language: build_editor_assist_context(
                current_graph,
                node_id=current_node_id,
                prop_name=current_prop,
                language=current_lang,
            )
        )
        return widget

    @staticmethod
    def _set_choice_widget_tooltip(node: F8StudioBaseNode, widget: QtWidgets.QWidget, prop_name: str) -> None:
        if not isinstance(widget, (F8OptionComboEditor, F8MultiSelectEditor, F8BoolSwitchEditor)):
            return
        desc = ""
        for field in _effective_state_fields(node):
            try:
                if str(field.name or "").strip() == prop_name:
                    desc = str(field.description or "").strip()
                    break
            except (AttributeError, TypeError):
                continue
        if not desc:
            return
        try:
            widget.set_context_tooltip(desc)
        except AttributeError:
            return

    def _populate_state_tab(
        self,
        *,
        host: Any,
        node: F8StudioBaseNode,
        spec: Any,
        model: Any,
        common_props: dict[str, Any],
        widget_factory: NodePropertyWidgetFactory,
        missing_locked: bool,
        inspect_mode: bool,
    ) -> None:
        tab_windows = self._tab_windows(host)
        prop_window = tab_windows["State"]
        if not isinstance(prop_window, _F8StateStackContainer):
            return
        if spec is None:
            can_add_state = False
            can_delete_state = False
        else:
            can_add_state = _policy_can_add(spec, "stateFields")
            can_delete_state = _policy_can_delete(spec, "stateFields")
        prop_window.set_add_visible(bool(can_add_state) and not missing_locked and not inspect_mode)
        prop_window.set_drag_enabled(not missing_locked and not inspect_mode)
        values = dict(model.custom_properties)
        eff_fields = _effective_state_fields(node)
        if not eff_fields and spec is not None:
            try:
                eff_fields = list(spec.stateFields or [])
            except Exception:
                eff_fields = []
        for field in eff_fields:
            try:
                name = str(field.name or "").strip()
            except Exception:
                name = ""
            if not name or name not in values:
                continue
            value = values.get(name)
            wid_type = self._widget_type_for_property(model, name, self._state_field_names(spec))
            if wid_type == 0:
                continue
            widget = _build_state_panel_control(
                node=node,
                prop_name=name,
                widget_type=wid_type,
                widget_factory=widget_factory,
                register_option_pool_dependent=lambda pool, current_widget: self._register_option_pool_dependent(
                    host,
                    pool,
                    current_widget,
                ),
            )
            tooltip = None
            common_prop = common_props.get(name)
            if isinstance(common_prop, dict):
                tooltip = common_prop.get("tooltip")
            access = _state_field_access(node, name)
            read_only = (
                access == F8StateAccess.ro
                or state_input_is_connected(node, name)
                or missing_locked
                or inspect_mode
            )
            _set_read_only_widget(widget, read_only=bool(read_only))
            allow_delete = bool(
                can_delete_state
                and not missing_locked
                and not inspect_mode
                and _policy_can_delete_state_field(field)
            )
            label_txt = str(field.label or "").strip()
            desc_txt = str(field.description or "").strip()
            show_on_node = bool(field.showOnNode)
            prop_window.add_widget(
                name=name,
                widget=widget,
                value=value,
                label=(label_txt or name).replace("_", " "),
                tooltip=desc_txt or tooltip,
                allow_delete=allow_delete,
                show_on_node=show_on_node,
                allow_edit=True,
                allow_show_on_node_toggle=bool(not missing_locked and not inspect_mode),
                edit_tooltip="View state field..." if inspect_mode else "Edit state field...",
            )
            if not inspect_mode:
                self._connect_widget_signals(host, widget)

    def _populate_property_tab(
        self,
        *,
        host: Any,
        node: F8StudioBaseNode,
        tab_name: str,
        items: list[tuple[Any, Any]],
        common_props: dict[str, Any],
        widget_factory: NodePropertyWidgetFactory,
        missing_locked: bool,
        inspect_mode: bool,
        state_field_names: set[str],
    ) -> None:
        tab_windows = self._tab_windows(host)
        prop_window = tab_windows[tab_name]
        for raw_prop_name, value in items:
            prop_name = str(raw_prop_name)
            wid_type = self._widget_type_for_property(node.model, prop_name, state_field_names)
            if wid_type == 0:
                continue
            widget = self._build_property_widget(
                host=host,
                node=node,
                prop_name=prop_name,
                widget_type=wid_type,
                widget_factory=widget_factory,
            )
            common_prop = common_props.get(prop_name)
            tooltip = None
            if isinstance(common_prop, dict):
                tooltip = self._apply_common_property_metadata(widget, common_prop, prop_name)
            if wid_type == NodePropWidgetEnum.QTEXT_EDIT.value and self._is_json_state_value(node, prop_name):
                widget = _F8JsonValueEditor(host)
                widget.set_name(prop_name)
            try:
                code_widget = self._build_code_editor_widget(host=host, node=node, prop_name=prop_name)
            except Exception:
                logger.exception("Failed to build code editor widget for property '%s'", prop_name)
                code_widget = None
            if code_widget is not None:
                widget = code_widget
            access = _state_field_access(node, prop_name)
            if access == F8StateAccess.ro or missing_locked or inspect_mode:
                _apply_read_only_widget(widget)
            self._set_choice_widget_tooltip(node, widget, prop_name)
            prop_window.add_widget(
                name=prop_name,
                widget=widget,
                value=value,
                label=prop_name.replace("_", " "),
                tooltip=tooltip,
            )
            if not inspect_mode and not isinstance(widget, _F8CodeButtonEditor):
                self._connect_widget_signals(host, widget)

    def _populate_node_tab(
        self,
        *,
        host: Any,
        node: F8StudioBaseNode,
        spec: Any,
        model: Any,
        widget_factory: NodePropertyWidgetFactory,
        missing_locked: bool,
        inspect_mode: bool,
    ) -> None:
        host.add_tab("Node")
        prop_window = self._tab_windows(host)["Node"]
        default_props = {
            "color": "Node base color.",
            "text_color": "Node text color.",
            "border_color": "Node border color.",
            "disabled": "Disable/Enable node state.",
            "id": "Unique identifier string to the node.",
        }
        for prop_name, tooltip in default_props.items():
            wid_type = model.get_widget_type(prop_name)
            widget = widget_factory.get_widget(wid_type)
            if isinstance(widget, QtWidgets.QWidget):
                widget = self._adopt_widget_parent(widget, host)
            widget_api = self._widget_api(widget)
            widget_api.set_name(prop_name)
            prop_window.add_widget(
                name=prop_name,
                widget=widget,
                value=model.get_property(prop_name),
                label=prop_name.replace("_", " "),
                tooltip=tooltip,
            )
            if inspect_mode:
                _apply_read_only_widget(widget)
            else:
                self._connect_widget_signals(host, widget)
        if isinstance(spec, F8OperatorSpec):
            svc_id = str(node.svcId or "")
            sys_widget = PropLabel(host)
            sys_widget.set_name("__sys_svcId")
            prop_window.add_widget(
                name="__sys_svcId",
                widget=sys_widget,
                value=svc_id,
                label="svcId",
                tooltip="Bound service container id.",
            )
        node_graph = node.graph
        if isinstance(node_graph, F8StudioGraph):
            layer_widget = F8LayerMembershipEditor(node_graph=node_graph, parent=host)
            layer_widget.set_name("f8_ui_state")
            prop_window.add_widget(
                name="f8_ui_state",
                widget=layer_widget,
                value=node.ui_state(),
                label="Layers",
                tooltip="Display-only graph layers for this node. A node can belong to multiple layers.",
            )
            layer_widget.setEnabled(bool(not inspect_mode and not missing_locked))
            if not inspect_mode:
                layer_widget.value_changed.connect(host._on_property_changed)
        purpose_widget = _F8InlineCodeEditor(host, language="plaintext")
        purpose_widget.set_name("nodePurpose")
        prop_window.add_widget(
            name="nodePurpose",
            widget=purpose_widget,
            value=str(node.nodePurpose or ""),
            label="Purpose",
            tooltip="Instance-specific purpose for this node in the current graph. Used by AI/collaboration context.",
        )
        if inspect_mode:
            _apply_read_only_widget(purpose_widget)
        else:
            purpose_widget.value_changed.connect(host._on_property_changed)

    def _attach_spec_tabs(self, *, host: Any, node: F8StudioBaseNode, spec: Any, inspect_mode: bool) -> None:
        if not isinstance(spec, (F8OperatorSpec, F8ServiceSpec)):
            return
        tab_widget = self._tab_widget(host)
        if self._should_show_commands_tab(spec):
            cmd_editor = _F8SpecCommandEditor(host, node=node, on_apply=host._on_spec_applied, inspect_mode=inspect_mode)
            tab_widget.addTab(cmd_editor, "Command")
        spec_ports = _F8SpecPortEditor(host, node=node, on_apply=host._on_spec_applied, inspect_mode=inspect_mode)
        tab_widget.addTab(spec_ports, "Port")

    def _hide_empty_property_tabs(self, host: Any) -> None:
        tab_widget = self._tab_widget(host)
        tab_windows = self._tab_windows(host)
        tab_index = {tab_widget.tabText(index): index for index in range(tab_widget.count())}
        for tab_name, prop_window in tab_windows.items():
            if prop_window.get_all_widgets():
                continue
            try:
                tab_widget.setTabVisible(tab_index[tab_name], False)
            except Exception:
                tab_widget.removeTab(tab_index[tab_name])

    def _select_default_tab(self, host: Any) -> None:
        tab_widget = self._tab_widget(host)
        self._reorder_tabs(tab_widget, ["State", "Command", "Port", "Node"])
        preferred_default: int | None = None
        for tab_name in ["State", "Command", "Port", "Node"]:
            for index in range(tab_widget.count()):
                if tab_widget.tabText(index) == tab_name:
                    preferred_default = index
                    break
            if preferred_default is not None:
                break
        tab_widget.setCurrentIndex(preferred_default if preferred_default is not None else 0)

    def _read_node(self, node: F8StudioBaseNode):
        host = cast(Any, self)
        model = node.model
        graph = node.graph
        if graph is None:
            raise RuntimeError("Property editor requires node.graph")
        graph_model = graph.model
        missing_locked, _missing_type = node_missing_lock_info(node)
        inspect_mode = bool(host._inspect_mode)
        common_props = graph_model.get_node_common_properties(node.type_) or {}
        spec = get_node_spec(node)
        state_field_names = self._state_field_names(spec)
        tab_mapping: dict[str, list[tuple[Any, Any]]] = defaultdict(list)
        for prop_name, prop_val in model.custom_properties.items():
            tab_name = self._tab_name_for_property(model, str(prop_name), state_field_names)
            tab_mapping[tab_name].append((prop_name, prop_val))
        reserved_tabs = {"Node", "Port", "Command"}
        for tab_name in sorted(tab_mapping.keys()):
            if tab_name in reserved_tabs:
                logger.warning("Property tab name '%s' is reserved by NodePropertyEditor; skipping tab.", tab_name)
                continue
            host.add_tab(tab_name)
        widget_factory = NodePropertyWidgetFactory()
        for tab_name in sorted(tab_mapping.keys()):
            if tab_name == "State":
                self._populate_state_tab(
                    host=host,
                    node=node,
                    spec=spec,
                    model=model,
                    common_props=common_props,
                    widget_factory=widget_factory,
                    missing_locked=missing_locked,
                    inspect_mode=inspect_mode,
                )
                continue
            self._populate_property_tab(
                host=host,
                node=node,
                tab_name=tab_name,
                items=tab_mapping[tab_name],
                common_props=common_props,
                widget_factory=widget_factory,
                missing_locked=missing_locked,
                inspect_mode=inspect_mode,
                state_field_names=state_field_names,
            )
        self._populate_node_tab(
            host=host,
            node=node,
            spec=spec,
            model=model,
            widget_factory=widget_factory,
            missing_locked=missing_locked,
            inspect_mode=inspect_mode,
        )
        host.type_wgt.setText(model.get_property("type_") or "")
        self._attach_spec_tabs(host=host, node=node, spec=spec, inspect_mode=inspect_mode)
        self._hide_empty_property_tabs(host)
        self._select_default_tab(host)
        return None
