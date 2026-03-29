from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any

from f8pysdk import (
    F8OperatorSpec,
    F8ServiceSpec,
    F8StateAccess,
    F8StateSpec,
    can_add as _policy_can_add,
    can_delete as _policy_can_delete,
    can_edit_existing as _policy_can_edit_existing,
)
from f8pysdk.msgspec_codec import copy_model

from NodeGraphQt import PropertiesBinWidget
from NodeGraphQt.constants import NodeEnum, NodePropWidgetEnum
from NodeGraphQt.custom_widgets.properties_bin.node_property_factory import NodePropertyWidgetFactory
from NodeGraphQt.custom_widgets.properties_bin.node_property_widgets import PropLineEdit
from NodeGraphQt.custom_widgets.properties_bin.prop_widgets_base import PropLabel

from qtpy import QtCore, QtGui, QtWidgets

from ...components.state_editors import (
    F8BoolSwitchEditor,
    F8CodeButtonEditor as _F8CodeButtonEditor,
    F8InlineCodeEditor as _F8InlineCodeEditor,
    F8JsonValueEditor as _F8JsonValueEditor,
    F8MultiSelectEditor,
    F8OptionComboEditor,
)
from ..studio_node_code_editor import get_node_text, set_node_text, studio_session_key
from ..spec_mutations import (
    add_state_field as _spec_add_state_field,
    delete_state_field as _spec_delete_state_field,
    replace_state_field as _spec_replace_state_field,
)
from ..state_controls import (
    build_state_panel_control as _build_state_panel_control,
    effective_state_fields as _effective_state_fields,
    schema_type_any as _schema_type,
    state_field_access as _state_field_access,
    state_field_ui_control as _state_field_ui_control,
)
from ...ui_control import parse_ui_control
from ..ui_override_mutations import (
    find_base_state_field as _find_base_state_field,
    remove_list_order_entry as _remove_list_order_entry,
    rename_list_order_entry as _rename_list_order_entry,
    set_list_order_override as _set_list_order_override,
    set_state_field_ui_override as _set_state_field_ui_override,
)
from ..ui_state_mutations import (
    set_state_field_global_hotkey_override as _set_state_field_global_hotkey_override,
    state_field_global_hotkey as _state_field_global_hotkey,
)
from .commands import _F8SpecCommandEditor
from .common import (
    _PROPERTY_PANEL_MIN_WIDTH,
    _TAB_HEADER_STYLE,
    _apply_read_only_widget,
    _build_editor_assist_context,
    _get_node_spec,
    _node_missing_lock_info,
    _package_attr,
    _schema_from_json_obj,
    _set_read_only_widget,
    _state_input_is_connected,
    _wrap_tab_page,
)
from .containers import _F8LabeledStackContainer, _F8StateContainer, _F8StateStackContainer
from .ports import _F8EditStateFieldDialog, _F8SpecPortEditor


logger = logging.getLogger(__name__)


def _should_show_commands_tab(spec: F8OperatorSpec | F8ServiceSpec) -> bool:
    command_specs = list(spec.commands or [])
    if command_specs:
        return True
    return _policy_can_add(spec, "commands")


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


class F8StudioNodePropEditorWidget(QtWidgets.QWidget):
    """
    Node properties editor widget for display a Node object.

    Args:
        parent (QtWidgets.QWidget): parent object.
        node (NodeGraphQt.NodeObject): node.
    """

    #: signal (node_id, prop_name, prop_value)
    property_changed = QtCore.Signal(str, str, object)
    property_changing = QtCore.Signal(str, str, object)
    property_closed = QtCore.Signal(str)

    def __init__(self, parent=None, node=None):
        super(F8StudioNodePropEditorWidget, self).__init__(parent)
        self._node = node
        self.__node_id = node.id
        self.__tab_windows = {}
        self.__tab = QtWidgets.QTabWidget(self)
        self.__tab.setObjectName("f8NodePropTabs")
        self.__tab.setDocumentMode(False)
        self.__tab.setStyleSheet(_TAB_HEADER_STYLE)
        self.__tab.setUsesScrollButtons(False)
        self.__tab.tabBar().setExpanding(True)
        self.__tab.tabBar().setElideMode(QtCore.Qt.TextElideMode.ElideRight)
        self.__tab.setMinimumWidth(_PROPERTY_PANEL_MIN_WIDTH)
        self._option_pool_dependents: dict[str, list[Any]] = {}
        self._reload_pending = False
        self._reload_debounce_ms = 50
        self._reload_timer = QtCore.QTimer(self)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.timeout.connect(self._reload_now)

        close_btn = QtWidgets.QPushButton(self)
        close_btn.setIcon(QtGui.QIcon(self.style().standardIcon(QtWidgets.QStyle.SP_DialogCloseButton)))
        close_btn.setFixedSize(24, 24)
        close_btn.setToolTip("close property")
        close_btn.clicked.connect(self._on_close)
        close_btn.setStyleSheet(
            "QPushButton { border: 0; border-radius: 4px; padding: 0; background: rgba(255,255,255,0.04); }"
            "QPushButton:hover { background: rgba(255,255,255,0.08); }"
        )

        pixmap = QtGui.QPixmap()
        if node.icon():
            pixmap = QtGui.QPixmap(node.icon())

            if pixmap.size().height() > NodeEnum.ICON_SIZE.value:
                pixmap = pixmap.scaledToHeight(NodeEnum.ICON_SIZE.value, QtCore.Qt.SmoothTransformation)
            if pixmap.size().width() > NodeEnum.ICON_SIZE.value:
                pixmap = pixmap.scaledToWidth(NodeEnum.ICON_SIZE.value, QtCore.Qt.SmoothTransformation)

        self.icon_label = QtWidgets.QLabel(self)
        self.icon_label.setPixmap(pixmap)
        self.icon_label.setStyleSheet("background: transparent;")

        self._name_label = QtWidgets.QLabel("name", self)
        self._name_label.setStyleSheet("color: rgba(235,235,235,140); font-size: 11px;")

        self.name_wgt = PropLineEdit(self)
        self.name_wgt.set_name("name")
        self.name_wgt.setToolTip("name\nSet the node name.")
        self.name_wgt.set_value(node.name())
        self.name_wgt.value_changed.connect(self._on_property_changed)
        self.name_wgt.setMinimumHeight(26)

        self.type_wgt = QtWidgets.QLabel(node.type_, self)
        self.type_wgt.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self.type_wgt.setToolTip("type_\nNode type identifier followed by the class name.")
        font = self.type_wgt.font()
        font.setPointSize(9)
        self.type_wgt.setFont(font)
        self.type_wgt.setStyleSheet("color: rgba(235,235,235,120); padding: 0 2px;")

        name_layout = QtWidgets.QHBoxLayout()
        name_layout.setContentsMargins(0, 0, 0, 0)
        name_layout.setSpacing(4)
        name_layout.addWidget(self.icon_label)
        name_layout.addWidget(self._name_label)
        name_layout.addWidget(self.name_wgt)
        name_layout.addWidget(close_btn)
        missing_locked, missing_type = _node_missing_lock_info(node)
        self._missing_banner = QtWidgets.QLabel(self)
        self._missing_banner.setStyleSheet(
            "color: rgb(255, 224, 138); background: rgba(80, 60, 0, 58); border-radius: 4px; padding: 2px 6px;"
        )
        self._missing_banner.setVisible(bool(missing_locked))
        if missing_locked:
            self._missing_banner.setText(f"Missing dependency: {missing_type or 'unknown type'}")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        layout.addLayout(name_layout)
        layout.addWidget(self._missing_banner)
        layout.addWidget(self.__tab)
        layout.addWidget(self.type_wgt)

        self._port_connections = self._read_node(node)
        if missing_locked:
            self._apply_missing_lock_read_only()

    def __repr__(self):
        return "<{} object at {}>".format(self.__class__.__name__, hex(id(self)))

    def _apply_missing_lock_read_only(self) -> None:
        try:
            self.name_wgt.setDisabled(True)
            self.name_wgt.setToolTip("Locked: missing dependency")
        except (AttributeError, RuntimeError, TypeError):
            logger.exception("Failed to set name widget readonly on missing-locked node")
        for widget in self.get_all_property_widgets():
            if widget is self.name_wgt:
                continue
            try:
                _set_read_only_widget(widget, read_only=True)
            except Exception:
                try:
                    widget.setEnabled(False)
                except Exception:
                    logger.exception("Failed to lock property widget for missing-locked node")

    def _on_close(self):
        """
        called by the close button.
        """
        self.property_closed.emit(self.__node_id)

    def _on_property_changed(self, name, value):
        """
        slot function called when a property widget has changed.

        Args:
            name (str): property name.
            value (object): new value.
        """
        self.property_changed.emit(self.__node_id, name, value)
        self.refresh_option_pool(str(name or ""))

    def _on_property_changing(self, name, value):
        """
        slot function called when a property widget is being scrubbed/previewed.

        Args:
            name (str): property name.
            value (object): new value (preview).
        """
        self.property_changing.emit(self.__node_id, name, value)
        self.refresh_option_pool(str(name or ""))

    def refresh_option_pool(self, pool_name: str) -> None:
        """
        Refresh option widgets that depend on a given pool state field.
        """
        pool = str(pool_name or "").strip()
        if not pool:
            return
        if pool not in self._option_pool_dependents:
            return
        for w in list(self._option_pool_dependents.get(pool) or []):
            try:
                w.refresh_options()
            except (AttributeError, RuntimeError, TypeError):
                continue

    def open_state_field_editor(self, field_name: str) -> None:
        """
        Open the edit dialog for a state field and apply changes.
        """
        missing_locked, _missing_type = _node_missing_lock_info(self._node)
        name = str(field_name or "").strip()
        if not name:
            return
        node = self._node
        if node is None:
            return
        spec = _get_node_spec(node)
        if spec is None:
            return

        # Find current effective field + base field.
        eff_fields = _effective_state_fields(node)
        if not eff_fields:
            try:
                eff_fields = list(spec.stateFields or [])
            except Exception:
                eff_fields = []
        current = None
        for f in eff_fields:
            try:
                if str(f.name or "").strip() == name:
                    current = f
                    break
            except (AttributeError, TypeError):
                continue
        if current is None:
            return

        can_edit_existing = _policy_can_edit_existing(spec, "stateFields")
        ui_only = not can_edit_existing

        # If UI-only, we still want to allow editing UI fields (showOnNode/uiControl/etc).
        dialog_type = _package_attr("_F8EditStateFieldDialog", _F8EditStateFieldDialog)
        hotkey_controller = getattr(getattr(node, "graph", None), "global_hotkey_controller", None)
        dlg = dialog_type(
            self,
            title="Edit state field",
            field=current,
            global_hotkey=_state_field_global_hotkey(node, name),
            current_binding_id=f"{str(node.id or '').strip()}:{name}",
            hotkey_conflict_lookup=(
                hotkey_controller.entries_for_hotkey if hotkey_controller is not None else None
            ),
            hotkey_capture_started=(hotkey_controller.suspend_hotkeys if hotkey_controller is not None else None),
            hotkey_capture_finished=(hotkey_controller.resume_hotkeys if hotkey_controller is not None else None),
            ui_only=ui_only,
            lock_identity_fields=bool(can_edit_existing),
            read_only=bool(missing_locked),
        )
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return
        new_field = dlg.field()
        global_hotkey = dlg.global_hotkey()

        if ui_only:
            self._apply_state_field_ui_override(name, new_field)
        else:
            self._apply_state_field_spec_replace(name, new_field)
        self._apply_state_field_global_hotkey_override(name, str(new_field.name or name), global_hotkey)

        self._on_spec_applied()

    def add_state_field(self) -> None:
        missing_locked, _missing_type = _node_missing_lock_info(self._node)
        if missing_locked:
            return
        node = self._node
        if node is None:
            return
        spec = _get_node_spec(node)
        if not isinstance(spec, (F8ServiceSpec, F8OperatorSpec)):
            return
        if not _policy_can_add(spec, "stateFields"):
            return
        field = F8StateSpec(
            name="",
            valueSchema=_schema_from_json_obj({"type": "any"}),
            access=F8StateAccess.rw,
            required=False,
            showOnNode=False,
        )
        dialog_type = _package_attr("_F8EditStateFieldDialog", _F8EditStateFieldDialog)
        hotkey_controller = getattr(getattr(node, "graph", None), "global_hotkey_controller", None)
        dlg = dialog_type(
            self,
            title="Add state field",
            field=field,
            hotkey_conflict_lookup=(hotkey_controller.entries_for_hotkey if hotkey_controller is not None else None),
            hotkey_capture_started=(hotkey_controller.suspend_hotkeys if hotkey_controller is not None else None),
            hotkey_capture_finished=(hotkey_controller.resume_hotkeys if hotkey_controller is not None else None),
            ui_only=False,
        )
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return
        new_field = dlg.field()
        if not str(new_field.name or "").strip():
            return
        self._apply_state_field_spec_add(new_field)
        self._on_spec_applied()

    def delete_state_field(self, field_name: str) -> None:
        missing_locked, _missing_type = _node_missing_lock_info(self._node)
        if missing_locked:
            return
        name = str(field_name or "").strip()
        if not name:
            return
        node = self._node
        if node is None:
            return
        spec = _get_node_spec(node)
        if not isinstance(spec, (F8ServiceSpec, F8OperatorSpec)):
            return
        if not _policy_can_delete(spec, "stateFields"):
            return
        # required fields are protected
        eff = _effective_state_fields(node)
        if not eff:
            try:
                eff = list(spec.stateFields or [])
            except Exception:
                eff = []
        for f in eff:
            try:
                if str(f.name or "").strip() == name and bool(f.required):
                    return
            except (AttributeError, TypeError):
                continue
        if (
            QtWidgets.QMessageBox.question(self, "Delete state field", f"Delete '{name}'?")
            != QtWidgets.QMessageBox.Yes
        ):
            return
        self._apply_state_field_spec_delete(name)
        self._on_spec_applied()

    def _apply_state_field_spec_replace(self, old_name: str, new_field: F8StateSpec) -> None:
        node = self._node
        if node is None:
            return
        spec = _get_node_spec(node)
        if spec is None:
            return
        spec2 = _spec_replace_state_field(spec, old_name=old_name, new_field=new_field)
        if spec2 is not spec:
            node.spec = spec2
        old_field_name = str(old_name or "").strip()
        new_field_name = str(new_field.name or "").strip() or old_field_name
        if old_field_name and new_field_name and old_field_name != new_field_name:
            _rename_list_order_entry(
                node,
                key="stateFields",
                old_name=old_field_name,
                new_name=new_field_name,
                base_order=self._state_field_base_order(spec2),
                rebuild=False,
            )
        self._resync_node_from_spec()

    def _apply_state_field_spec_add(self, new_field: F8StateSpec) -> None:
        node = self._node
        if node is None:
            return
        spec = _get_node_spec(node)
        if spec is None:
            return
        spec2 = _spec_add_state_field(spec, field=new_field)
        if spec2 is not spec:
            node.spec = spec2
        self._resync_node_from_spec()

    def _apply_state_field_spec_delete(self, name: str) -> None:
        node = self._node
        if node is None:
            return
        spec = _get_node_spec(node)
        if spec is None:
            return
        spec2 = _spec_delete_state_field(spec, name=name)
        if spec2 is not spec:
            node.spec = spec2
        _remove_list_order_entry(
            node,
            key="stateFields",
            entry_name=str(name or "").strip(),
            base_order=self._state_field_base_order(spec2),
            rebuild=False,
        )
        self._resync_node_from_spec()

    def _apply_state_field_ui_override(self, name: str, edited: F8StateSpec) -> None:
        node = self._node
        if node is None:
            return
        spec = _get_node_spec(node)
        base = _find_base_state_field(spec, name=name) if spec is not None else None
        _set_state_field_ui_override(node, field_name=name, base=base or edited, edited=edited)

    def _apply_state_field_global_hotkey_override(self, old_name: str, new_name: str, hotkey: str) -> None:
        node = self._node
        if node is None:
            return
        old_field_name = str(old_name or "").strip()
        new_field_name = str(new_name or "").strip()
        if old_field_name and old_field_name != new_field_name:
            _set_state_field_global_hotkey_override(node, field_name=old_field_name, hotkey="")
        target_field_name = new_field_name or old_field_name
        if not target_field_name:
            return
        _set_state_field_global_hotkey_override(node, field_name=target_field_name, hotkey=hotkey)

    def _toggle_state_field_show_on_node(self, field_name: str, show_on_node: bool) -> None:
        missing_locked, _missing_type = _node_missing_lock_info(self._node)
        if missing_locked:
            return
        node = self._node
        if node is None:
            return
        name = str(field_name or "").strip()
        if not name:
            return
        spec = _get_node_spec(node)
        base = _find_base_state_field(spec, name=name) if spec is not None else None
        if base is None:
            base = F8StateSpec(name=name, valueSchema=_schema_from_json_obj({"type": "any"}), access=F8StateAccess.rw)
        edited = copy_model(base, deep=True)
        edited.showOnNode = bool(show_on_node)
        self._apply_state_field_ui_override(name, edited)

    def _state_field_base_order(self, spec: F8ServiceSpec | F8OperatorSpec | None = None) -> list[str]:
        current_spec = spec if spec is not None else _get_node_spec(self._node)
        if not isinstance(current_spec, (F8ServiceSpec, F8OperatorSpec)):
            return []
        ordered: list[str] = []
        for field in list(current_spec.stateFields or []):
            name = str(field.name or "").strip()
            if name:
                ordered.append(name)
        return ordered

    def _reorder_state_fields(self, ordered_names: list[str]) -> None:
        node = self._node
        if node is None:
            return
        missing_locked, _missing_type = _node_missing_lock_info(node)
        if missing_locked:
            return
        _set_list_order_override(
            node,
            key="stateFields",
            order=[str(name or "").strip() for name in list(ordered_names or [])],
            base_order=self._state_field_base_order(),
            rebuild=True,
        )

    def _resync_node_from_spec(self) -> None:
        node = self._node
        if node is None:
            return
        try:
            node.sync_from_spec()
        except Exception:
            logger.exception("sync_from_spec failed while applying state-field edits")

    def _read_node(self, node):
        """
        Populate widget from a node.

        Args:
            node (NodeGraphQt.BaseNode): node class.

        Returns:
            _PortConnectionsContainer: ports container widget.
        """
        model = node.model
        graph_model = node.graph.model
        missing_locked, _missing_type = _node_missing_lock_info(node)

        common_props = graph_model.get_node_common_properties(node.type_) or {}
        spec = _get_node_spec(node)
        state_field_names: set[str] = set()
        if spec is not None:
            try:
                for f in list(spec.stateFields or []):
                    name = str(f.name or "").strip()
                    if name:
                        state_field_names.add(name)
            except Exception:
                state_field_names = set()

        def _tab_name_for_property(prop_name: str) -> str:
            try:
                return str(model.get_tab_name(prop_name) or "Properties")
            except KeyError:
                if prop_name in state_field_names:
                    logger.warning("Missing tab metadata for state property '%s'; fallback to State tab.", prop_name)
                    return "State"
                logger.warning("Missing tab metadata for property '%s'; fallback to Properties tab.", prop_name)
                return "Properties"

        def _widget_type_for_property(prop_name: str) -> int:
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

        # sort tabs and properties.
        tab_mapping = defaultdict(list)
        for prop_name, prop_val in model.custom_properties.items():
            tab_name = _tab_name_for_property(str(prop_name))
            tab_mapping[tab_name].append((prop_name, prop_val))

        # add tabs.
        reserved_tabs = ["Node", "Port", "Command"]
        for tab in sorted(tab_mapping.keys()):
            if tab in reserved_tabs:
                print('tab name "{}" is reserved by the "NodePropWidget" ' "please use a different tab name.")
                continue
            self.add_tab(tab)

        # property widget factory.
        widget_factory = NodePropertyWidgetFactory()

        # populate tab properties.
        for tab in sorted(tab_mapping.keys()):
            prop_window = self.__tab_windows[tab]
            if tab == "State" and isinstance(prop_window, _F8StateStackContainer):
                # Build the State tab from stateFields so we can attach edit/delete/add UI.
                if spec is None:
                    can_add_state = False
                    can_delete_state = False
                else:
                    can_add_state = _policy_can_add(spec, "stateFields")
                    can_delete_state = _policy_can_delete(spec, "stateFields")
                prop_window.set_add_visible(bool(can_add_state) and not missing_locked)
                prop_window.set_drag_enabled(not missing_locked)
                # Map property values.
                values = dict(model.custom_properties)
                # Order by effective state fields (applies UI overrides).
                eff_fields = _effective_state_fields(node)
                if not eff_fields and spec is not None:
                    try:
                        eff_fields = list(spec.stateFields or [])
                    except Exception:
                        eff_fields = []
                for f in eff_fields:
                    try:
                        name = str(f.name or "").strip()
                    except Exception:
                        name = ""
                    if not name:
                        continue
                    if name not in values:
                        continue
                    value = values.get(name)
                    wid_type = _widget_type_for_property(name)
                    if wid_type == 0:
                        continue
                    widget = _build_state_panel_control(
                        node=node,
                        prop_name=name,
                        widget_type=wid_type,
                        widget_factory=widget_factory,
                        register_option_pool_dependent=lambda pool, w: self._option_pool_dependents.setdefault(
                            pool, []
                        ).append(w),
                    )

                    tooltip = None
                    if name in common_props.keys() and "tooltip" in common_props[name].keys():
                        tooltip = common_props[name]["tooltip"]
                    access = _state_field_access(node, name)
                    read_only = access == F8StateAccess.ro or _state_input_is_connected(node, name) or missing_locked
                    _set_read_only_widget(widget, read_only=bool(read_only))
                    required = bool(f.required)
                    allow_delete = bool(can_delete_state and not required)
                    label_txt = str(f.label or "").strip()
                    desc_txt = str(f.description or "").strip()
                    show_on_node = bool(f.showOnNode)
                    prop_window.add_widget(
                        name=name,
                        widget=widget,
                        value=value,
                        label=(label_txt or name).replace("_", " "),
                        tooltip=desc_txt or tooltip,
                        allow_delete=allow_delete,
                        show_on_node=bool(show_on_node),
                    )
                    widget.value_changed.connect(self._on_property_changed)
                    try:
                        widget.value_changing.connect(self._on_property_changing)
                    except (AttributeError, RuntimeError, TypeError):
                        pass
                continue
            for prop_name, value in tab_mapping[tab]:
                wid_type = _widget_type_for_property(str(prop_name))
                if wid_type == 0:
                    continue
                widget = _build_state_panel_control(
                    node=node,
                    prop_name=prop_name,
                    widget_type=wid_type,
                    widget_factory=widget_factory,
                    register_option_pool_dependent=lambda pool, w: self._option_pool_dependents.setdefault(
                        pool, []
                    ).append(w),
                )
                widget = _adopt_widget_parent(widget, self)

                tooltip = None
                if prop_name in common_props.keys():
                    if "items" in common_props[prop_name].keys():
                        widget.set_items(common_props[prop_name]["items"])
                    if "range" in common_props[prop_name].keys():
                        prop_range = common_props[prop_name]["range"]
                        try:
                            widget.set_min(prop_range[0])
                            widget.set_max(prop_range[1])
                        except (AttributeError, RuntimeError, TypeError, ValueError):
                            try:
                                widget.setMinimum(prop_range[0])
                                widget.setMaximum(prop_range[1])
                            except (AttributeError, RuntimeError, TypeError, ValueError):
                                logger.exception("Failed to apply numeric range for property '%s'", prop_name)
                    if "tooltip" in common_props[prop_name].keys():
                        tooltip = common_props[prop_name]["tooltip"]

                if wid_type == NodePropWidgetEnum.QTEXT_EDIT.value and _is_json_state_value(node, prop_name):
                    widget = _F8JsonValueEditor(self)
                    widget.set_name(prop_name)

                # Dialog-backed code editor (eg. python_script code).
                try:
                    ui_control_raw = _state_field_ui_control(node, prop_name)
                    parsed_ui = parse_ui_control(ui_control_raw)
                    ui_control = parsed_ui.control_name
                    if ui_control == "code":
                        ui_language = parsed_ui.ui_language
                        widget = _F8CodeButtonEditor(
                            self,
                            title=f"{node.name()} - {prop_name}",
                            language=ui_language or "plaintext",
                        )
                        widget.set_name(prop_name)
                        graph = None
                        node_id = ""
                        try:
                            graph = node.graph
                            node_id = str(node.id or "").strip()
                        except Exception:
                            graph = None
                            node_id = ""

                        warning_parent = None
                        try:
                            warning_parent = self.window() if self.window() is not None else self
                        except (AttributeError, RuntimeError, TypeError):
                            warning_parent = self

                        widget.set_persisted_value_getter(
                            lambda current_graph=graph, current_node_id=node_id, current_prop=str(
                                prop_name
                            ): get_node_text(current_graph, current_node_id, current_prop)
                        )
                        widget.set_persisted_value_setter(
                            lambda updated, current_graph=graph, current_node_id=node_id, current_prop=str(
                                prop_name
                            ), current_parent=warning_parent: set_node_text(
                                current_graph,
                                current_node_id,
                                current_prop,
                                str(updated or ""),
                                push_undo=True,
                                warning_parent=current_parent,
                            )
                        )

                        widget.set_editor_assist_context(
                            _build_editor_assist_context(
                                graph,
                                node_id=node_id,
                                prop_name=str(prop_name),
                                language=ui_language or "plaintext",
                            )
                        )
                        widget.set_editor_assist_context_provider(
                            lambda current_graph=graph, current_node_id=node_id, current_prop=str(
                                prop_name
                            ), current_lang=(ui_language or "plaintext"): _build_editor_assist_context(
                                current_graph,
                                node_id=current_node_id,
                                prop_name=current_prop,
                                language=current_lang,
                            )
                        )
                        widget.set_editor_session_key(studio_session_key(graph, node_id, str(prop_name)))
                except Exception:
                    logger.exception("Failed to build code editor widget for property '%s'", prop_name)
                access = _state_field_access(node, prop_name)
                if access == F8StateAccess.ro or missing_locked:
                    _apply_read_only_widget(widget)
                # Enrich tooltips for option/switch editors.
                if isinstance(widget, (F8OptionComboEditor, F8MultiSelectEditor, F8BoolSwitchEditor)):
                    desc = ""
                    for f in _effective_state_fields(node):
                        try:
                            if str(f.name or "").strip() == str(prop_name):
                                desc = str(f.description or "").strip()
                                break
                        except (AttributeError, TypeError):
                            continue
                    if desc:
                        try:
                            widget.set_context_tooltip(desc)
                        except AttributeError:
                            pass
                prop_window.add_widget(
                    name=prop_name, widget=widget, value=value, label=prop_name.replace("_", " "), tooltip=tooltip
                )
                if not isinstance(widget, _F8CodeButtonEditor):
                    widget.value_changed.connect(self._on_property_changed)
                    try:
                        widget.value_changing.connect(self._on_property_changing)
                    except (AttributeError, RuntimeError, TypeError):
                        pass

        # add "Node" tab properties. (default props)
        self.add_tab("Node")
        default_props = {
            "color": "Node base color.",
            "text_color": "Node text color.",
            "border_color": "Node border color.",
            "disabled": "Disable/Enable node state.",
            "id": "Unique identifier string to the node.",
        }
        prop_window = self.__tab_windows["Node"]
        for prop_name, tooltip in default_props.items():
            wid_type = model.get_widget_type(prop_name)
            widget = widget_factory.get_widget(wid_type)
            if isinstance(widget, QtWidgets.QWidget):
                widget = _adopt_widget_parent(widget, self)
            widget.set_name(prop_name)
            prop_window.add_widget(
                name=prop_name,
                widget=widget,
                value=model.get_property(prop_name),
                label=prop_name.replace("_", " "),
                tooltip=tooltip,
            )

            widget.value_changed.connect(self._on_property_changed)
            try:
                widget.value_changing.connect(self._on_property_changing)
            except (AttributeError, RuntimeError, TypeError):
                pass

        spec = _get_node_spec(node)
        if isinstance(spec, F8OperatorSpec):
            try:
                svc_id = str(node.svcId or "")  # type: ignore[attr-defined]
            except Exception:
                svc_id = ""
            sys_widget = PropLabel(self)
            sys_widget.set_name("__sys_svcId")
            prop_window.add_widget(
                name="__sys_svcId",
                widget=sys_widget,
                value=svc_id,
                label="svcId",
                tooltip="Bound service container id.",
            )

        purpose_widget = _F8InlineCodeEditor(self, language="plaintext")
        purpose_widget.set_name("nodePurpose")
        try:
            node_purpose = str(node.nodePurpose or "")  # type: ignore[attr-defined]
        except Exception:
            node_purpose = ""
        prop_window.add_widget(
            name="nodePurpose",
            widget=purpose_widget,
            value=node_purpose,
            label="Purpose",
            tooltip="Instance-specific purpose for this node in the current graph. Used by AI/collaboration context.",
        )
        purpose_widget.value_changed.connect(self._on_property_changed)

        self.type_wgt.setText(model.get_property("type_") or "")

        # built-in spec editors (if node has F8 spec).
        if isinstance(spec, (F8OperatorSpec, F8ServiceSpec)):
            if _should_show_commands_tab(spec):
                cmd_editor = _F8SpecCommandEditor(self, node=node, on_apply=self._on_spec_applied)
                self.__tab.addTab(cmd_editor, "Command")
            spec_ports = _F8SpecPortEditor(self, node=node, on_apply=self._on_spec_applied)
            self.__tab.addTab(spec_ports, "Port")

        # hide/remove empty tabs with no property widgets.
        tab_index = {self.__tab.tabText(x): x for x in range(self.__tab.count())}
        current_idx = None
        for tab_name, prop_window in self.__tab_windows.items():
            prop_widgets = prop_window.get_all_widgets()
            if not prop_widgets:
                # I prefer to hide the tab but in older version of pyside this
                # attribute doesn't exist we'll just remove.
                try:
                    self.__tab.setTabVisible(tab_index[tab_name], False)
                except Exception:
                    self.__tab.removeTab(tab_index[tab_name])
                continue
            if current_idx is None:
                current_idx = tab_index[tab_name]

        # Order: State, Commands, Port, Node (Node last).
        _reorder_tabs(self.__tab, ["State", "Command", "Port", "Node"])

        # Default tab: first existing among preferred, else 0.
        preferred_default = None
        for t in ["State", "Command", "Port", "Node"]:
            for i in range(self.__tab.count()):
                if self.__tab.tabText(i) == t:
                    preferred_default = i
                    break
            if preferred_default is not None:
                break
        self.__tab.setCurrentIndex(preferred_default if preferred_default is not None else 0)

        return None

    def _on_spec_applied(self) -> None:
        node = self._node
        if node is None:
            return
        try:
            node.sync_from_spec()
        except Exception:
            logger.exception("sync_from_spec failed before reload")
        self.reload()

    def reload(self) -> None:
        """
        Coalesce multiple reload requests into a single UI rebuild.

        Some services update state at high frequency; rebuilding the entire
        properties UI per update can freeze the UI and exhaust native window
        handles if removed widgets are not properly released.
        """
        if self._reload_pending:
            return
        self._reload_pending = True
        # Debounced rebuild to coalesce bursts of updates.
        self._reload_timer.start(int(self._reload_debounce_ms))

    def _clear_tabs(self) -> None:
        # `removeTab()` does not delete the widget.
        # Avoid `setParent(None)` to prevent transient top-level window flashes.
        while self.__tab.count():
            w = self.__tab.widget(0)
            self.__tab.removeTab(0)
            if w is None:
                continue
            try:
                w.setVisible(False)
            except Exception:
                logger.exception("Failed to hide tab widget before deleteLater")
            w.deleteLater()

    def _reload_now(self) -> None:
        self._reload_pending = False
        node = self._node
        if node is None:
            return
        prev_tab = None
        scroll_pos: dict[str, int] = {}
        try:
            idx = self.__tab.currentIndex()
            if idx >= 0:
                prev_tab = self.__tab.tabText(idx)
        except Exception:
            prev_tab = None
        try:
            for i in range(self.__tab.count()):
                tab_name = self.__tab.tabText(i)
                w = self.__tab.widget(i)
                if not w:
                    continue
                areas = w.findChildren(QtWidgets.QScrollArea)
                if not areas:
                    continue
                try:
                    scroll_pos[tab_name] = int(areas[0].verticalScrollBar().value())
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    pass
        except Exception:
            scroll_pos = {}

        self._clear_tabs()
        self.__tab_windows = {}
        self._option_pool_dependents = {}
        self._port_connections = self._read_node(node)
        missing_locked, missing_type = _node_missing_lock_info(node)
        try:
            self._missing_banner.setVisible(bool(missing_locked))
            if missing_locked:
                self._missing_banner.setText(f"Missing dependency: {missing_type or 'unknown type'}")
        except (AttributeError, RuntimeError, TypeError):
            logger.exception("Failed to update missing banner")
        if missing_locked:
            self._apply_missing_lock_read_only()
        if prev_tab:
            try:
                for i in range(self.__tab.count()):
                    if self.__tab.tabText(i) == prev_tab:
                        self.__tab.setCurrentIndex(i)
                        break
            except (AttributeError, RuntimeError, TypeError):
                pass
        if scroll_pos:

            def _restore() -> None:
                for i in range(self.__tab.count()):
                    tab_name = self.__tab.tabText(i)
                    if tab_name not in scroll_pos:
                        continue
                    w = self.__tab.widget(i)
                    if not w:
                        continue
                    areas = w.findChildren(QtWidgets.QScrollArea)
                    if not areas:
                        continue
                    try:
                        areas[0].verticalScrollBar().setValue(scroll_pos[tab_name])
                    except (AttributeError, RuntimeError, TypeError):
                        pass

            QtCore.QTimer.singleShot(0, _restore)

    def node_id(self):
        """
        Returns the node id linked to the widget.

        Returns:
            str: node id
        """
        return self.__node_id

    def add_widget(self, name, widget, tab="Properties"):
        """
        add new node property widget.

        Args:
            name (str): property name.
            widget (BaseProperty): property widget.
            tab (str): tab name.
        """
        if tab not in self.__tab_windows.keys():
            tab = "Properties"
        if tab not in self.__tab_windows.keys():
            self.add_tab(tab)
        window = self.__tab_windows[tab]
        window.add_widget(name, widget)
        widget.value_changed.connect(self._on_property_changed)
        try:
            widget.value_changing.connect(self._on_property_changing)
        except (AttributeError, RuntimeError, TypeError):
            pass

    def add_tab(self, name):
        """
        add a new tab.

        Args:
            name (str): tab name.

        Returns:
            PropListWidget: tab child widget.
        """
        if name in self.__tab_windows.keys():
            raise AssertionError("Tab name {} already taken!".format(name))
        if name == "State":
            window = _F8StateStackContainer(self)
        elif name == "Node":
            window = _F8LabeledStackContainer(self)
        else:
            window = _F8StateContainer(self)
        self.__tab_windows[name] = window
        if name == "State":
            assert isinstance(window, _F8StateStackContainer)
            window.edit_state_field_requested.connect(self.open_state_field_editor)
            window.delete_state_field_requested.connect(self.delete_state_field)
            window.add_state_field_requested.connect(self.add_state_field)
            window.toggle_state_field_show_on_node_requested.connect(self._toggle_state_field_show_on_node)
            window.state_field_order_changed.connect(self._reorder_state_fields)
        self.__tab.addTab(_wrap_tab_page(window), name)
        return window

    def get_tab_widget(self):
        """
        Returns the underlying tab widget.

        Returns:
            QtWidgets.QTabWidget: tab widget.
        """
        return self.__tab

    def get_widget(self, name):
        """
        get property widget.

        Args:
            name (str): property name.

        Returns:
            NodeGraphQt.custom_widgets.properties_bin.prop_widgets_abstract.BaseProperty: property widget.
        """
        if name == "name":
            return self.name_wgt
        for prop_win in self.__tab_windows.values():
            widget = prop_win.get_widget(name)
            if widget:
                return widget

    def get_all_property_widgets(self):
        """
        get all the node property widgets.

        Returns:
            list[BaseProperty]: property widgets.
        """
        widgets = [self.name_wgt]
        for prop_win in self.__tab_windows.values():
            for widget in prop_win.get_all_widgets().values():
                widgets.append(widget)
        return widgets

    def get_port_connection_widget(self):
        """
        Returns the ports connections container widget.

        Returns:
            _PortConnectionsContainer: port container widget.
        """
        return self._port_connections

    def set_port_lock_widgets_disabled(self, disabled=True):
        """
        Enable/Disable port lock column widgets.

        Args:
            disabled (bool): true to disable checkbox.
        """
        return


class F8StudioPropertiesBinWidget(PropertiesBinWidget):
    """
    Customized Properties Bin Widget for F8PyStudio.
    """

    def __init__(self, parent=None, node_graph=None):
        super(F8StudioPropertiesBinWidget, self).__init__(parent=parent, node_graph=node_graph)

    def create_property_editor(self, node):
        return F8StudioNodePropEditorWidget(node=node)


class F8StudioSingleNodePropertiesWidget(QtWidgets.QWidget):
    """
    Single-node properties panel (no PropertiesBinWidget/QTableWidget).

    NodeGraphQt's PropertiesBinWidget hosts editors inside a QTableWidget, which
    can scroll-jump on focus/click. Since Studio only needs one active editor,
    we present a single `F8StudioNodePropEditorWidget` inside a QScrollArea.
    """

    def __init__(self, parent: QtWidgets.QWidget | None = None, *, node_graph: Any) -> None:
        super().__init__(parent)
        self._node_graph = node_graph
        self._node_id: str | None = None
        self._editor: F8StudioNodePropEditorWidget | None = None
        self._block_signal = False
        self._last_node_click_ts: float = 0.0
        self._selection_timer = QtCore.QTimer(self)
        self._selection_timer.setSingleShot(True)
        self._selection_timer.timeout.connect(self._apply_graph_selection)

        self._scroll = QtWidgets.QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumWidth(_PROPERTY_PANEL_MIN_WIDTH)

        self._container = QtWidgets.QWidget(self._scroll)
        self._container.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        self._container_layout = QtWidgets.QVBoxLayout(self._container)
        self._container_layout.setContentsMargins(0, 0, 0, 0)
        self._container_layout.setSpacing(0)

        self._empty = QtWidgets.QLabel("Select a node to view properties.", self._container)
        self._empty.setAlignment(QtCore.Qt.AlignCenter)
        self._empty.setStyleSheet("color: rgba(235,235,235,140); padding: 14px;")
        self._container_layout.addWidget(self._empty, 1)

        self._scroll.setWidget(self._container)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._scroll, 1)

        self._wire_graph_signals()
        QtCore.QTimer.singleShot(0, self._sync_container_width)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._sync_container_width()

    def _sync_container_width(self) -> None:
        """
        Keep the content widget width aligned to the scroll viewport width.

        This prevents QScrollArea from showing a horizontal scrollbar due to
        the container/editor having a slightly larger size hint.
        """
        try:
            vp_w = int(self._scroll.viewport().width())
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return
        if vp_w <= 0:
            return
        try:
            self._container.setMinimumWidth(vp_w)
        except (AttributeError, RuntimeError, TypeError):
            logger.exception("Failed to sync property panel container width")

    def _wire_graph_signals(self) -> None:
        g = self._node_graph
        if g is None:
            return
        g.node_selected.connect(self._on_node_selected)  # type: ignore[attr-defined]
        g.node_double_clicked.connect(self._on_node_selected)  # type: ignore[attr-defined]
        g.node_selection_changed.connect(self._on_node_selection_changed)  # type: ignore[attr-defined]
        g.nodes_deleted.connect(self._on_nodes_deleted)  # type: ignore[attr-defined]
        g.property_changed.connect(self._on_graph_property_changed)  # type: ignore[attr-defined]
        g.port_connected.connect(self._on_graph_ports_changed)  # type: ignore[attr-defined]
        g.port_disconnected.connect(self._on_graph_ports_changed)  # type: ignore[attr-defined]

    def _on_graph_ports_changed(self, _in_port: Any, _out_port: Any) -> None:
        """
        Toggle read-only state for State-tab widgets when state-edge bindings change.
        """
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
        if self._editor is None or self._node_id is None:
            return
        g = self._node_graph
        if g is None:
            return
        node = g.get_node_by_id(self._node_id)  # type: ignore[attr-defined]
        if node is None:
            return
        spec = _get_node_spec(node)
        if spec is None:
            return
        eff_fields = _effective_state_fields(node)
        if not eff_fields:
            eff_fields = list(spec.stateFields or [])

        for f in eff_fields:
            name = str(f.name or "").strip()
            if not name:
                continue
            w = self._editor.get_widget(name)
            if w is None:
                continue
            access = _state_field_access(node, name)
            read_only = access == F8StateAccess.ro or _state_input_is_connected(node, name)
            _set_read_only_widget(w, read_only=bool(read_only))

    def _clear_editor(self, *, clear_node_id: bool = True) -> None:
        if clear_node_id:
            self._node_id = None
        editor = self._editor
        if editor is not None:
            self._editor = None
            self._container_layout.removeWidget(editor)
            try:
                editor.setVisible(False)
            except (AttributeError, RuntimeError, TypeError):
                logger.exception("Failed to hide editor before deleteLater")
            editor.deleteLater()
        try:
            self._empty.setVisible(True)
        except (AttributeError, RuntimeError, TypeError):
            logger.exception("Failed to show empty editor placeholder")

    def _set_editor(self, editor: F8StudioNodePropEditorWidget) -> None:
        # Preserve the node id that the caller (set_node) just set. We are
        # swapping the editor widget, not clearing the selection.
        self._clear_editor(clear_node_id=False)
        self._editor = editor
        try:
            self._empty.setVisible(False)
        except (AttributeError, RuntimeError, TypeError):
            logger.exception("Failed to hide empty editor placeholder")
        self._container_layout.addWidget(editor, 0)
        self._sync_container_width()
        try:
            editor.property_changed.connect(self._on_editor_property_changed)  # type: ignore[attr-defined]
        except (AttributeError, RuntimeError, TypeError):
            logger.exception("Failed to connect editor.property_changed")
        try:
            editor.property_changing.connect(self._on_editor_property_changing)  # type: ignore[attr-defined]
        except (AttributeError, RuntimeError, TypeError):
            logger.exception("Failed to connect editor.property_changing")
        try:
            editor.property_closed.connect(self._on_editor_closed)  # type: ignore[attr-defined]
        except (AttributeError, RuntimeError, TypeError):
            logger.exception("Failed to connect editor.property_closed")

    def set_node(self, node: Any | None, *, force_clear: bool = False) -> None:
        if node is None:
            # Avoid transient clear -> re-set flicker caused by selection jitter.
            # Only clear when explicitly forced (eg. node deleted) or when the
            # panel is currently empty.
            if force_clear or self._editor is None:
                self._clear_editor(clear_node_id=True)
            return
        try:
            node_id = str(node.id or "")
        except Exception:
            node_id = ""
        if not node_id:
            self._clear_editor(clear_node_id=True)
            return
        if self._node_id == node_id and self._editor is not None:
            return
        self._node_id = node_id
        self._set_editor(F8StudioNodePropEditorWidget(self._container, node=node))
        try:
            self._scroll.verticalScrollBar().setValue(0)
        except (AttributeError, RuntimeError, TypeError):
            logger.exception("Failed to reset property panel scroll position")

    def _on_node_selected(self, node: Any) -> None:
        self._last_node_click_ts = time.monotonic()
        self.set_node(node)

    def _on_node_selection_changed(self, selected: list[Any], _deselected: list[Any]) -> None:
        # NodeGraphQt can emit transient selection updates (eg. deselect then
        # select). Clicking on embedded widgets inside a node can also cause
        # selection to briefly clear. Debounce and query the final selection.
        try:
            self._selection_timer.start(0)
        except Exception:
            # Fallback: behave like the default signal payload.
            if selected:
                self.set_node(selected[0])

    def _on_nodes_deleted(self, node_ids: list[str]) -> None:
        if not self._node_id:
            return
        if self._node_id in set(str(x) for x in (node_ids or [])):
            self.set_node(None, force_clear=True)

    def _on_editor_closed(self, _node_id: str) -> None:
        # User closed the editor explicitly; clear the view.
        self.set_node(None, force_clear=True)

    def _apply_graph_selection(self) -> None:
        """
        Apply the current graph selection to the properties panel.

        Keep showing the last node when selection is empty.

        Some embedded node controls (eg. inline state expand/collapse) can
        temporarily clear selection during the click sequence. Clearing the
        panel on empty selection causes a visible flash. Since Studio only
        needs a single active properties view, keep the last shown node until
        another node is selected or the node is deleted.
        """
        g = self._node_graph
        selected_nodes: list[Any] = []
        if g is not None:
            try:
                selected_nodes = list(g.selected_nodes() or [])  # type: ignore[attr-defined]
            except Exception:
                selected_nodes = []
        if selected_nodes:
            self.set_node(selected_nodes[0])
            return
        # No selection: keep current panel content (do not clear).
        return

    def _on_editor_property_changed(self, node_id: str, prop_name: str, prop_value: Any) -> None:
        if self._block_signal:
            return
        g = self._node_graph
        if g is None:
            return
        nid = str(node_id or "").strip()
        if not nid:
            return
        try:
            node = g.get_node_by_id(nid)  # type: ignore[attr-defined]
        except Exception:
            node = None
        if node is None:
            return
        try:
            node.set_property(prop_name, prop_value, push_undo=True)
        except Exception:
            logger.exception("set_property failed nodeId=%s prop=%s", nid, prop_name)

    def _on_editor_property_changing(self, node_id: str, prop_name: str, prop_value: Any) -> None:
        if self._block_signal:
            return
        g = self._node_graph
        if g is None:
            return
        nid = str(node_id or "").strip()
        if not nid:
            return
        try:
            node = g.get_node_by_id(nid)  # type: ignore[attr-defined]
        except Exception:
            node = None
        if node is None:
            return
        try:
            node.set_property(prop_name, prop_value, push_undo=False)
        except Exception:
            logger.exception("set_property preview failed nodeId=%s prop=%s", nid, prop_name)

    def _on_graph_property_changed(self, node: Any, prop_name: str, prop_value: Any) -> None:
        """
        Keep UI in sync when node properties are updated externally (runtime sync, undo, etc.).
        """
        if self._editor is None or self._node_id is None:
            return
        try:
            if str(node.id or "") != self._node_id:
                return
        except AttributeError:
            return
        prop_key = str(prop_name or "").strip()
        if not prop_key:
            return

        if prop_key in {"f8_spec", "f8_ui_overrides", "f8_ui_state"}:
            self._editor.reload()
            return

        # Always try pool refresh even if the pool field has no visible editor widget.
        try:
            self._editor.refresh_option_pool(prop_key)
        except Exception:
            logger.exception("refresh_option_pool failed for key=%s", prop_key)

        w = self._editor.get_widget(prop_name)
        if w is None:
            return
        try:
            cur = w.get_value()
        except (AttributeError, RuntimeError, TypeError):
            cur = None
        if cur == prop_value:
            return
        self._block_signal = True
        try:
            w.set_value(prop_value)
        except Exception:
            logger.exception("Failed to update property widget value key=%s", prop_key)
        finally:
            self._block_signal = False


def _is_json_state_value(node: Any, prop_name: str) -> bool:
    """
    True if the property is a state field whose schema is object/array/any.
    """
    spec = _get_node_spec(node)
    if spec is None:
        return False
    try:
        fields = list(spec.stateFields or [])
    except Exception:
        fields = []
    for f in fields:
        try:
            if str(f.name or "").strip() != prop_name:
                continue
        except (AttributeError, TypeError):
            continue
        try:
            vs = f.valueSchema
        except Exception:
            vs = None
        return _schema_type(vs) in {"object", "array", "any"}
    return False


def _reorder_tabs(tab_widget: QtWidgets.QTabWidget, preferred: list[str]) -> None:
    """
    Reorder tabs so `preferred` (if present) are first in that order,
    then any remaining tabs (in their current relative order).
    """
    if tab_widget.count() <= 1:
        return

    current = [tab_widget.tabText(i) for i in range(tab_widget.count())]
    preferred_present = [t for t in preferred if t in current]
    rest = [t for t in current if t not in preferred_present]
    target = preferred_present + rest

    # Move tabs into target order using the tab bar.
    bar = tab_widget.tabBar()
    for dst, name in enumerate(target):
        for src in range(bar.count()):
            if tab_widget.tabText(src) == name:
                if src != dst:
                    bar.moveTab(src, dst)
                break
