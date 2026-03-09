from __future__ import annotations

import logging
from typing import Any

from qtpy import QtCore, QtWidgets
from NodeGraphQt import BaseNode

from f8pysdk import F8OperatorSpec, F8ServiceSpec
from f8pysdk.nats_naming import ensure_token

from ..constants import STUDIO_SERVICE_ID
from ..ui_notifications import show_warning

logger = logging.getLogger(__name__)


class GraphIdentityActionsMixin:
    def install_identity_context_menu_for_nodes(self, node_classes: list[type]) -> None:
        nodes_menu = self.context_nodes_menu()
        if nodes_menu is None:
            return
        for node_cls in list(node_classes or []):
            node_type = str(node_cls.type_ or "")
            if not node_type or node_type in self._identity_menu_node_types:
                continue
            nodes_menu.add_command(
                self.tr("Rename Id..."),
                func=self._on_rename_id_menu_action,
                node_type=node_type,
            )
            self._identity_menu_node_types.add(node_type)

    def _on_rename_id_menu_action(self, graph: Any, node: Any) -> None:
        _ = graph
        if not isinstance(node, BaseNode):
            return
        try:
            spec = node.spec  # type: ignore[attr-defined]
        except (AttributeError, RuntimeError, TypeError):
            return
        if not isinstance(spec, (F8OperatorSpec, F8ServiceSpec)):
            return

        ok_stop, stop_msg = self._is_node_rename_allowed_when_stopped(node)
        if not ok_stop:
            show_warning(self._notification_parent(), self.tr("Rename Id Failed"), stop_msg)
            return

        title = self.tr("Rename ServiceId") if isinstance(spec, F8ServiceSpec) else self.tr("Rename OperatorId")
        label = self.tr("Id Name:")
        current_id = str(node.id or "").strip()
        new_id = self._prompt_id_rename_dialog(title=title, label=label, value=current_id)
        if new_id is None:
            return

        ok_id, id_msg = self._validate_new_node_id(node=node, new_id=new_id)
        if not ok_id:
            show_warning(self._notification_parent(), self.tr("Rename Id Failed"), id_msg)
            return

        ok_rename, rename_msg = self._rename_node_identity(node=node, new_id=new_id)
        if not ok_rename:
            show_warning(self._notification_parent(), self.tr("Rename Id Failed"), rename_msg)

    @staticmethod
    def _prompt_id_rename_dialog(*, title: str, label: str, value: str) -> str | None:
        dialog = QtWidgets.QDialog(None)
        dialog.setWindowTitle(str(title or "Rename Id"))
        dialog.resize(420, 100)

        id_edit = QtWidgets.QLineEdit(str(value or ""), dialog)
        form = QtWidgets.QFormLayout()
        form.addRow(str(label or "Id名字:"), id_edit)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel,
            parent=dialog,
        )
        buttons.accepted.connect(dialog.accept)  # type: ignore[attr-defined]
        buttons.rejected.connect(dialog.reject)  # type: ignore[attr-defined]

        layout = QtWidgets.QVBoxLayout(dialog)
        layout.addLayout(form)
        layout.addWidget(buttons)

        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return None
        out = str(id_edit.text() or "").strip()
        if not out:
            return None
        return out

    def _validate_new_node_id(self, *, node: BaseNode, new_id: str) -> tuple[bool, str]:
        candidate = str(new_id or "").strip()
        if not candidate:
            return False, self.tr("Id cannot be empty.")
        try:
            ensure_token(candidate, label="node_id")
        except ValueError as exc:
            return False, self.tr("Invalid Id format: {}").format(exc)

        old_id = str(node.id or "").strip()
        if candidate == old_id:
            return False, self.tr("New Id is the same as the current Id.")

        try:
            spec = node.spec  # type: ignore[attr-defined]
        except (AttributeError, RuntimeError, TypeError):
            spec = None
        if isinstance(spec, F8ServiceSpec) and old_id == STUDIO_SERVICE_ID:
            return False, self.tr("Built-in service Id `{}` cannot be renamed.").format(STUDIO_SERVICE_ID)

        existing = self.get_node_by_id(candidate)
        if existing is not None and existing is not node:
            return False, self.tr("Id `{}` already exists.").format(candidate)
        return True, ""

    def _is_node_rename_allowed_when_stopped(self, node: BaseNode) -> tuple[bool, str]:
        try:
            spec = node.spec  # type: ignore[attr-defined]
        except (AttributeError, RuntimeError, TypeError):
            return False, self.tr("This node type does not support Id rename.")
        bridge = self._service_bridge
        if bridge is None:
            return True, ""

        if isinstance(spec, F8ServiceSpec):
            service_id = str(node.id or "").strip()
            if not service_id:
                return False, self.tr("Current service node is missing serviceId.")
            try:
                running = bool(bridge.is_service_running(service_id))
            except (AttributeError, RuntimeError, TypeError, ValueError):
                running = False
            if running:
                return False, self.tr("Service `{}` is running. Stop it before renaming Id.").format(service_id)
            return True, ""

        if isinstance(spec, F8OperatorSpec):
            try:
                service_id = str(node.svcId or "").strip()  # type: ignore[attr-defined]
            except (AttributeError, RuntimeError, TypeError):
                service_id = ""
            if not service_id:
                return False, self.tr("Operator is not bound to a serviceId.")
            try:
                running = bool(bridge.is_service_running(service_id))
            except (AttributeError, RuntimeError, TypeError, ValueError):
                running = False
            if running:
                return False, self.tr("Operator service `{}` is running. Stop it before renaming Id.").format(
                    service_id
                )
            return True, ""

        return False, self.tr("This node type does not support Id rename.")

    def _update_node_id_mapping(self, *, node: BaseNode, old_id: str, new_id: str) -> None:
        model_nodes = self.model.nodes
        if old_id:
            model_nodes.pop(old_id, None)
        model_nodes[str(new_id)] = node

    def _rewrite_connected_port_node_id_references(self, *, old_id: str, new_id: str) -> None:
        src = str(old_id or "").strip()
        dst = str(new_id or "").strip()
        if not src or not dst or src == dst:
            return
        for node in list(self.all_nodes() or []):
            ports = list(node.input_ports() or []) + list(node.output_ports() or [])
            for port in ports:
                connected = port.model.connected_ports
                if src not in connected:
                    continue
                moved = list(connected.pop(src) or [])
                if not moved:
                    continue
                dst_ports = connected.setdefault(dst, [])
                for name in moved:
                    if name not in dst_ports:
                        dst_ports.append(name)

    def repair_stale_port_connection_refs(self) -> int:
        """
        Drop connected-port references whose node_id is no longer present in graph.

        Returns:
            int: number of stale refs removed.
        """
        valid_node_ids = {str(n.id or "").strip() for n in list(self.all_nodes() or []) if str(n.id or "").strip()}
        removed = 0
        for node in list(self.all_nodes() or []):
            ports = list(node.input_ports() or []) + list(node.output_ports() or [])
            for port in ports:
                connected = port.model.connected_ports
                stale_ids = [nid for nid in list(connected.keys()) if str(nid or "") not in valid_node_ids]
                for stale_id in stale_ids:
                    removed += len(list(connected.pop(stale_id, []) or []))
        if removed:
            logger.warning("Repaired %s stale port connection reference(s).", removed)
        return removed

    def _cascade_service_id_to_bound_operators(
        self,
        *,
        old_service_id: str,
        new_service_id: str,
        service_class: str,
    ) -> None:
        old_sid = str(old_service_id or "").strip()
        new_sid = str(new_service_id or "").strip()
        expected_service_class = str(service_class or "")
        if not old_sid or not new_sid or old_sid == new_sid:
            return
        for n in list(self.all_nodes() or []):
            if not self._is_operator_node(n):
                continue
            try:
                op_spec = n.spec
            except (AttributeError, RuntimeError, TypeError):
                continue
            if not isinstance(op_spec, F8OperatorSpec):
                continue
            try:
                op_svc_id = str(n.svcId or "").strip()
            except (AttributeError, RuntimeError, TypeError):
                op_svc_id = ""
            if op_svc_id != old_sid:
                continue
            if str(op_spec.serviceClass or "") != expected_service_class:
                continue
            try:
                n.svcId = new_sid  # type: ignore[attr-defined]
            except (AttributeError, RuntimeError, TypeError):
                continue
            try:
                if "svcId" in n.model.properties or "svcId" in n.model.custom_properties:
                    n.set_property("svcId", new_sid, push_undo=False)
            except (AttributeError, RuntimeError, TypeError):
                pass

    @staticmethod
    def _refresh_service_identity_bindings(node: BaseNode) -> None:
        view = node.view
        try:
            refresh = view.refresh_service_identity_bindings  # type: ignore[attr-defined]
        except (AttributeError, RuntimeError, TypeError):
            refresh = None
        if callable(refresh):
            try:
                refresh()
            except (AttributeError, RuntimeError, TypeError):
                pass
        try:
            view.draw_node()
        except (AttributeError, RuntimeError, TypeError):
            try:
                view.update()
            except (AttributeError, RuntimeError, TypeError):
                pass

    def _rename_node_identity(self, *, node: BaseNode, new_id: str) -> tuple[bool, str]:
        old_id = str(node.id or "").strip()
        nid = str(new_id or "").strip()
        if not old_id or not nid:
            return False, self.tr("Invalid node Id.")
        if old_id == nid:
            return False, self.tr("New Id is the same as the current Id.")

        try:
            spec = node.spec  # type: ignore[attr-defined]
        except (AttributeError, RuntimeError, TypeError):
            return False, self.tr("This node type does not support Id rename.")

        # NodeGraphQt stores connections as {node_id: [port_name]} on each port model.
        # Rewrite these refs before replacing the model node-id mapping.
        self._rewrite_connected_port_node_id_references(old_id=old_id, new_id=nid)
        self._update_node_id_mapping(node=node, old_id=old_id, new_id=nid)
        node.model.id = nid
        node.view.id = nid
        self.repair_stale_port_connection_refs()

        if isinstance(spec, F8OperatorSpec):
            try:
                if "operatorId" in node.model.properties or "operatorId" in node.model.custom_properties:
                    node.set_property("operatorId", nid, push_undo=False)
            except (AttributeError, RuntimeError, TypeError):
                pass
            return True, ""

        if isinstance(spec, F8ServiceSpec):
            try:
                node.svcId = nid  # type: ignore[attr-defined]
            except (AttributeError, RuntimeError, TypeError):
                pass
            try:
                if "svcId" in node.model.properties or "svcId" in node.model.custom_properties:
                    node.set_property("svcId", nid, push_undo=False)
            except (AttributeError, RuntimeError, TypeError):
                pass
            self._cascade_service_id_to_bound_operators(
                old_service_id=old_id,
                new_service_id=nid,
                service_class=str(spec.serviceClass or ""),
            )
            old_timer = self._reclaim_timers.pop(old_id, None)
            if old_timer is not None:
                remaining_ms = -1
                try:
                    remaining_ms = int(old_timer.remainingTime())
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    remaining_ms = -1
                try:
                    old_timer.stop()
                    old_timer.deleteLater()
                except (AttributeError, RuntimeError, TypeError):
                    pass
                new_timer = QtCore.QTimer(self)
                new_timer.setSingleShot(True)
                new_timer.timeout.connect(lambda _sid=nid: self._reclaim_service_if_unreferenced(_sid))  # type: ignore[attr-defined]
                self._reclaim_timers[nid] = new_timer
                if remaining_ms > 0:
                    try:
                        new_timer.start(remaining_ms)
                    except (AttributeError, RuntimeError, TypeError, ValueError):
                        pass
            self._refresh_service_identity_bindings(node)
            return True, ""

        return False, self.tr("This node type does not support Id rename.")

