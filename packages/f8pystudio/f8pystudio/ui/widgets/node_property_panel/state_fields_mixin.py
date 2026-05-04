from __future__ import annotations

import logging
from typing import Any, cast

from f8pysdk.codec import copy_model
from f8pysdk.specs import (
    F8OperatorSpec,
    F8ServiceSpec,
    F8StateAccess,
    F8StateSpec,
    can_add as _policy_can_add,
    can_delete_state_field as _policy_can_delete_state_field,
    can_delete as _policy_can_delete,
    can_edit_existing as _policy_can_edit_existing,
)
from qtpy import QtWidgets

from ....nodegraph.node_graph import F8StudioGraph
from ....nodegraph.node_base import F8StudioBaseNode
from ....nodegraph.spec_mutations import (
    add_state_field as _spec_add_state_field,
    delete_state_field as _spec_delete_state_field,
    replace_state_field as _spec_replace_state_field,
)
from ....nodegraph.state_schema import effective_state_fields as _effective_state_fields
from ....nodegraph.ui_override_mutations import (
    find_base_state_field as _find_base_state_field,
    remove_list_order_entry as _remove_list_order_entry,
    rename_list_order_entry as _rename_list_order_entry,
    set_list_order_override as _set_list_order_override,
    set_state_field_ui_override as _set_state_field_ui_override,
)
from ....nodegraph.ui_state_mutations import (
    set_state_field_global_hotkey_override as _set_state_field_global_hotkey_override,
    state_field_global_hotkey as _state_field_global_hotkey,
)
from ...dialogs.node_spec_edit_dialogs import _F8EditStateFieldDialog
from ...support.node_property_support import get_node_spec, node_missing_lock_info, schema_from_json_obj_loose

logger = logging.getLogger(__name__)


class NodePropertyStateFieldsMixin:
    @staticmethod
    def _inspect_mode_enabled(host: Any) -> bool:
        try:
            return bool(host._inspect_mode)
        except AttributeError:
            return False

    @staticmethod
    def _state_field_dialog_cls(host: Any) -> Any:
        try:
            return type(host)._STATE_FIELD_DIALOG_CLS
        except AttributeError:
            return _F8EditStateFieldDialog

    @staticmethod
    def _node_hotkey_controller(node: F8StudioBaseNode) -> Any | None:
        try:
            graph = node.graph
        except AttributeError:
            return None
        if not isinstance(graph, F8StudioGraph):
            return None
        return graph.global_hotkey_controller

    def open_state_field_editor(self, field_name: str) -> None:
        host = cast(Any, self)
        missing_locked, _missing_type = node_missing_lock_info(host._node)
        inspect_mode = NodePropertyStateFieldsMixin._inspect_mode_enabled(host)
        name = str(field_name or "").strip()
        if not name:
            return
        node = host._node
        if node is None:
            return
        spec = get_node_spec(node)
        if spec is None:
            return

        eff_fields = _effective_state_fields(node)
        if not eff_fields:
            try:
                eff_fields = list(spec.stateFields or [])
            except Exception:
                eff_fields = []
        current = None
        for field in eff_fields:
            try:
                if str(field.name or "").strip() == name:
                    current = field
                    break
            except (AttributeError, TypeError):
                continue
        if current is None:
            return

        can_edit_existing = _policy_can_edit_existing(spec, "stateFields")
        ui_only = not can_edit_existing
        hotkey_controller = NodePropertyStateFieldsMixin._node_hotkey_controller(node)
        read_only = bool(missing_locked or inspect_mode)
        dlg = NodePropertyStateFieldsMixin._state_field_dialog_cls(host)(
            host,
            title="View state field" if read_only else "Edit state field",
            field=current,
            global_hotkey=_state_field_global_hotkey(node, name),
            current_binding_id=f"{str(node.id or '').strip()}:{name}",
            hotkey_conflict_lookup=(hotkey_controller.entries_for_hotkey if hotkey_controller is not None else None),
            hotkey_capture_started=(hotkey_controller.suspend_hotkeys if hotkey_controller is not None else None),
            hotkey_capture_finished=(hotkey_controller.resume_hotkeys if hotkey_controller is not None else None),
            ui_only=ui_only,
            lock_identity_fields=False,
            read_only=read_only,
        )
        if dlg.exec_() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        new_field = dlg.field()
        global_hotkey = dlg.global_hotkey()

        if ui_only:
            host._apply_state_field_ui_override(name, new_field)
        else:
            host._apply_state_field_spec_replace(name, new_field)
        host._apply_state_field_global_hotkey_override(name, str(new_field.name or name), global_hotkey)
        host._on_spec_applied()

    def add_state_field(self) -> None:
        host = cast(Any, self)
        missing_locked, _missing_type = node_missing_lock_info(host._node)
        inspect_mode = NodePropertyStateFieldsMixin._inspect_mode_enabled(host)
        if missing_locked or inspect_mode:
            return
        node = host._node
        if node is None:
            return
        spec = get_node_spec(node)
        if not isinstance(spec, (F8ServiceSpec, F8OperatorSpec)):
            return
        if not _policy_can_add(spec, "stateFields"):
            return
        field = F8StateSpec(
            name="",
            valueSchema=schema_from_json_obj_loose({"type": "any"}),
            access=F8StateAccess.rw,
            required=False,
            showOnNode=False,
        )
        hotkey_controller = NodePropertyStateFieldsMixin._node_hotkey_controller(node)
        dlg = NodePropertyStateFieldsMixin._state_field_dialog_cls(host)(
            host,
            title="Add state field",
            field=field,
            hotkey_conflict_lookup=(hotkey_controller.entries_for_hotkey if hotkey_controller is not None else None),
            hotkey_capture_started=(hotkey_controller.suspend_hotkeys if hotkey_controller is not None else None),
            hotkey_capture_finished=(hotkey_controller.resume_hotkeys if hotkey_controller is not None else None),
            ui_only=False,
        )
        if dlg.exec_() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        new_field = dlg.field()
        if not str(new_field.name or "").strip():
            return
        host._apply_state_field_spec_add(new_field)
        host._on_spec_applied()

    def delete_state_field(self, field_name: str) -> None:
        host = cast(Any, self)
        missing_locked, _missing_type = node_missing_lock_info(host._node)
        inspect_mode = NodePropertyStateFieldsMixin._inspect_mode_enabled(host)
        if missing_locked or inspect_mode:
            return
        name = str(field_name or "").strip()
        if not name:
            return
        node = host._node
        if node is None:
            return
        spec = get_node_spec(node)
        if not isinstance(spec, (F8ServiceSpec, F8OperatorSpec)):
            return
        if not _policy_can_delete(spec, "stateFields"):
            return
        eff_fields = _effective_state_fields(node)
        if not eff_fields:
            try:
                eff_fields = list(spec.stateFields or [])
            except Exception:
                eff_fields = []
        current_field: F8StateSpec | None = None
        for field in eff_fields:
            try:
                field_name = str(field.name or "").strip()
            except (AttributeError, TypeError):
                continue
            if field_name == name:
                current_field = field
                break
        if current_field is not None and not _policy_can_delete_state_field(current_field):
            return
        if (
            QtWidgets.QMessageBox.question(host, "Delete state field", f"Delete '{name}'?")
            != QtWidgets.QMessageBox.StandardButton.Yes
        ):
            return
        host._apply_state_field_spec_delete(name)
        host._on_spec_applied()

    def _apply_state_field_spec_replace(self, old_name: str, new_field: F8StateSpec) -> None:
        host = cast(Any, self)
        node = host._node
        if node is None:
            return
        spec = get_node_spec(node)
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
                base_order=host._state_field_base_order(spec2),
                rebuild=False,
            )
        host._resync_node_from_spec()

    def _apply_state_field_spec_add(self, new_field: F8StateSpec) -> None:
        host = cast(Any, self)
        node = host._node
        if node is None:
            return
        spec = get_node_spec(node)
        if spec is None:
            return
        spec2 = _spec_add_state_field(spec, field=new_field)
        if spec2 is not spec:
            node.spec = spec2
        host._resync_node_from_spec()

    def _apply_state_field_spec_delete(self, name: str) -> None:
        host = cast(Any, self)
        node = host._node
        if node is None:
            return
        spec = get_node_spec(node)
        if spec is None:
            return
        spec2 = _spec_delete_state_field(spec, name=name)
        if spec2 is not spec:
            node.spec = spec2
        _remove_list_order_entry(
            node,
            key="stateFields",
            entry_name=str(name or "").strip(),
            base_order=host._state_field_base_order(spec2),
            rebuild=False,
        )
        host._resync_node_from_spec()

    def _apply_state_field_ui_override(self, name: str, edited: F8StateSpec) -> None:
        host = cast(Any, self)
        node = host._node
        if node is None:
            return
        spec = get_node_spec(node)
        base = _find_base_state_field(spec, name=name) if spec is not None else None
        _set_state_field_ui_override(node, field_name=name, base=base or edited, edited=edited)

    def _apply_state_field_global_hotkey_override(self, old_name: str, new_name: str, hotkey: str) -> None:
        host = cast(Any, self)
        node = host._node
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
        host = cast(Any, self)
        missing_locked, _missing_type = node_missing_lock_info(host._node)
        inspect_mode = NodePropertyStateFieldsMixin._inspect_mode_enabled(host)
        if missing_locked or inspect_mode:
            return
        node = host._node
        if node is None:
            return
        name = str(field_name or "").strip()
        if not name:
            return
        spec = get_node_spec(node)
        base = _find_base_state_field(spec, name=name) if spec is not None else None
        if base is None:
            base = F8StateSpec(
                name=name, valueSchema=schema_from_json_obj_loose({"type": "any"}), access=F8StateAccess.rw
            )
        edited = copy_model(base, deep=True)
        edited.showOnNode = bool(show_on_node)
        host._apply_state_field_ui_override(name, edited)

    def _state_field_base_order(self, spec: F8ServiceSpec | F8OperatorSpec | None = None) -> list[str]:
        host = cast(Any, self)
        current_spec = spec if spec is not None else get_node_spec(host._node)
        if not isinstance(current_spec, (F8ServiceSpec, F8OperatorSpec)):
            return []
        ordered: list[str] = []
        for field in list(current_spec.stateFields or []):
            name = str(field.name or "").strip()
            if name:
                ordered.append(name)
        return ordered

    def _reorder_state_fields(self, ordered_names: list[str]) -> None:
        host = cast(Any, self)
        node = host._node
        if node is None:
            return
        missing_locked, _missing_type = node_missing_lock_info(node)
        inspect_mode = NodePropertyStateFieldsMixin._inspect_mode_enabled(host)
        if missing_locked or inspect_mode:
            return
        _set_list_order_override(
            node,
            key="stateFields",
            order=[str(name or "").strip() for name in list(ordered_names or [])],
            base_order=host._state_field_base_order(),
            rebuild=True,
        )

    def _resync_node_from_spec(self) -> None:
        host = cast(Any, self)
        node = host._node
        if node is None:
            return
        try:
            node.sync_from_spec()
        except Exception:
            logger.exception("sync_from_spec failed while applying state-field edits")
