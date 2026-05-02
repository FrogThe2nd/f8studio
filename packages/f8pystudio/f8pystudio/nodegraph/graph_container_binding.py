from __future__ import annotations

import logging
from typing import Any

from qtpy import QtCore, QtWidgets
from NodeGraphQt import BaseNode

from f8pysdk.specs import F8OperatorSpec

from .container_basenode import F8StudioContainerBaseNode
from .operator_basenode import F8StudioOperatorBaseNode
from .edge_rules import validate_runtime_connection
from f8pystudio.studio_specs.identifiers import SERVICE_CLASS as _CANVAS_SERVICE_CLASS_
from f8pystudio.studio_specs.identifiers import STUDIO_SERVICE_ID
from ..ui.support.ui_notifications import show_warning

_BASE_OPERATOR_CLS_ = F8StudioOperatorBaseNode
_BASE_CONTAINER_CLS_ = F8StudioContainerBaseNode

logger = logging.getLogger(__name__)


def _scene_rect(node: BaseNode) -> QtCore.QRectF | None:
    return node.view.sceneBoundingRect()


def _rect_at_pos(item: QtWidgets.QGraphicsItem, pos: list[float] | tuple[float, float]) -> QtCore.QRectF:
    """
    Compute a "scene-like" rect for an item positioned at `pos` (top-left),
    without requiring the item to be in a scene.
    """
    brect = item.boundingRect()
    return QtCore.QRectF(float(pos[0]), float(pos[1]), brect.width(), brect.height())


def _item_id(item: Any) -> str:
    if item is None:
        return ""
    return str(item.id or "").strip()


class GraphContainerBindingMixin:
    @staticmethod
    def _is_operator_node(node: Any) -> bool:
        try:
            return isinstance(node.spec, F8OperatorSpec)
        except Exception:
            return False

    @staticmethod
    def _is_container_node(node: Any) -> bool:
        return isinstance(node, _BASE_CONTAINER_CLS_)

    def _container_at_node(self, node: Any) -> _BASE_CONTAINER_CLS_ | None:
        r_node = _scene_rect(node)
        if r_node is None:
            return None
        return self._container_at_rect(r_node)

    def _container_at_rect(self, rect: QtCore.QRectF) -> _BASE_CONTAINER_CLS_ | None:
        for container in self.all_nodes():
            if not self._is_container_node(container):
                continue
            r_run = _scene_rect(container)
            if r_run is None:
                continue
            if r_run.intersects(rect):
                return container
        return None

    def _bind_operator_to_container(self, operator: _BASE_OPERATOR_CLS_, container: _BASE_CONTAINER_CLS_) -> bool:
        if not self._is_operator_node(operator):
            logger.warning("Cannot bind non-operator node to container")
            return False
        if not self._is_container_node(container):
            logger.warning("Cannot bind operator node to non-container node")
            return False
        if operator.spec.serviceClass != container.spec.serviceClass:
            logger.warning(
                f"Operator serviceClass '{operator.spec.serviceClass}' does not match container serviceClass '{container.spec.serviceClass}'"
            )
            return False

        sid = container.id
        if not sid:
            logger.error("Container node has no ID")
            return False
        operator.svcId = sid  # type: ignore[attr-defined]
        try:
            if "svcId" in operator.model.properties or "svcId" in operator.model.custom_properties:
                operator.set_property("svcId", str(sid), push_undo=False)
        except (AttributeError, RuntimeError, TypeError):
            pass
        container.add_child(operator)
        return True

    def _container_bound_nodes(self, container: _BASE_CONTAINER_CLS_) -> list[BaseNode]:
        """
        Return nodes that are bound to the container (best-effort).
        """
        out: list[BaseNode] = []

        # Prefer the node objects tracked by _BASE_CONTAINER_CLS_.
        for child in container._child_nodes:
            nid = child.id
            n = self.get_node_by_id(nid)
            if n is not None:
                out.append(n)

        # Fallback: view-level tracking.
        for view in container.view._child_views:
            nid = view.id
            n = self.get_node_by_id(nid)
            if n is not None:
                out.append(n)

        # Dedupe by id.
        return list({n.id: n for n in out}.values())

    def _expand_delete_nodes(self, nodes: list[Any]) -> list[Any]:
        """
        Expand delete list so deleting a container cascades to its child operators.
        """
        if not nodes:
            return []

        out: list[Any] = []
        seen: set[str] = set()

        def add_node_obj(n: Any) -> None:
            nid = n.id
            if nid in seen:
                return
            seen.add(nid)
            out.append(n)

            if self._is_container_node(n):
                for child in self._container_bound_nodes(n):
                    add_node_obj(child)

        for n in nodes:
            if n is None:
                continue
            add_node_obj(n)

        return out

    def _container_by_id(self, container_id: str) -> _BASE_CONTAINER_CLS_ | None:
        cid = str(container_id or "").strip()
        if not cid:
            return None
        node = self.get_node_by_id(cid)
        if node is None:
            return None
        if not self._is_container_node(node):
            return None
        return node

    @staticmethod
    def _set_node_scene_pos(node: Any, *, x: float, y: float) -> None:
        try:
            node.view.xy_pos = [float(x), float(y)]
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
        try:
            node.model.pos = [float(x), float(y)]
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass

    def _operator_current_container(self, operator: _BASE_OPERATOR_CLS_) -> _BASE_CONTAINER_CLS_ | None:
        container_ref: Any | None = operator.view._container_item

        if container_ref is not None:
            if self._is_container_node(container_ref):
                return container_ref
            container_id = _item_id(container_ref)
            if container_id:
                container = self._container_by_id(container_id)
                if container is not None:
                    return container

        try:
            service_id = str(operator.svcId or "").strip()
        except (AttributeError, RuntimeError, TypeError):
            service_id = ""
        return self._container_by_id(service_id)

    def _selected_operators_for_same_container_drop(
        self,
        *,
        anchor_operator: _BASE_OPERATOR_CLS_,
        target_container: _BASE_CONTAINER_CLS_,
    ) -> list[_BASE_OPERATOR_CLS_]:
        target_container_id = _item_id(target_container)
        if not target_container_id:
            return [anchor_operator]

        candidates: list[Any] = [anchor_operator]
        candidates.extend(list(self.selected_nodes() or []))

        operators: list[_BASE_OPERATOR_CLS_] = []
        seen_ids: set[str] = set()
        target_service_class = str(target_container.spec.serviceClass or "")
        for candidate in candidates:
            if candidate is None or not self._is_operator_node(candidate):
                continue
            candidate_operator = candidate
            candidate_id = _item_id(candidate_operator)
            if not candidate_id or candidate_id in seen_ids:
                continue
            service_class = str(candidate_operator.spec.serviceClass or "")
            if service_class == _CANVAS_SERVICE_CLASS_:
                continue
            if service_class != target_service_class:
                continue
            candidate_container = self._container_at_node(candidate_operator)
            if _item_id(candidate_container) != target_container_id:
                continue
            operators.append(candidate_operator)
            seen_ids.add(candidate_id)

        if anchor_operator not in operators:
            operators.insert(0, anchor_operator)
        return operators

    def _unbind_operator_from_container(
        self,
        *,
        operator: _BASE_OPERATOR_CLS_,
        container: _BASE_CONTAINER_CLS_ | None,
    ) -> None:
        if container is None:
            return
        try:
            container.remove_child(operator)
            return
        except (AttributeError, RuntimeError, TypeError):
            pass
        try:
            container._child_nodes = [n for n in list(container._child_nodes or []) if n is not operator and n.id != operator.id]
        except (AttributeError, RuntimeError, TypeError):
            pass
        try:
            container.view._child_views = [
                v for v in list(container.view._child_views or []) if v is not operator.view and v.id != operator.id
            ]
        except (AttributeError, RuntimeError, TypeError):
            pass
        try:
            if operator.view._container_item is container:
                operator.view._container_item = None
        except (AttributeError, RuntimeError, TypeError):
            pass

    def _disconnect_invalid_connections_for_operator(self, operator: _BASE_OPERATOR_CLS_) -> int:
        dropped = 0
        seen_pairs: set[tuple[int, int]] = set()
        for out_port in list(operator.output_ports() or []):
            for in_port in list(out_port.connected_ports() or []):
                key = (id(out_port), id(in_port))
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)
                allowed, _reason = validate_runtime_connection(
                    out_port_name=str(out_port.name() or ""),
                    in_port_name=str(in_port.name() or ""),
                    out_node=out_port.node(),
                    in_node=in_port.node(),
                )
                if allowed:
                    continue
                try:
                    out_port.disconnect_from(in_port, push_undo=False, emit_signal=False)
                    dropped += 1
                except (AttributeError, RuntimeError, TypeError):
                    continue
        for in_port in list(operator.input_ports() or []):
            for out_port in list(in_port.connected_ports() or []):
                key = (id(out_port), id(in_port))
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)
                allowed, _reason = validate_runtime_connection(
                    out_port_name=str(out_port.name() or ""),
                    in_port_name=str(in_port.name() or ""),
                    out_node=out_port.node(),
                    in_node=in_port.node(),
                )
                if allowed:
                    continue
                try:
                    out_port.disconnect_from(in_port, push_undo=False, emit_signal=False)
                    dropped += 1
                except (AttributeError, RuntimeError, TypeError):
                    continue
        return dropped

    def on_operator_drop(
        self,
        *,
        node_id: str,
        start_pos: tuple[float, float],
        start_container_id: str,
    ) -> tuple[bool, str]:
        nid = str(node_id or "").strip()
        if not nid:
            return False, "missing node id"
        node = self.get_node_by_id(nid)
        if node is None:
            return False, f"node not found: {nid}"
        if not self._is_operator_node(node):
            return True, ""

        operator = node
        service_class = str(operator.spec.serviceClass or "")
        if service_class == _CANVAS_SERVICE_CLASS_:
            return True, ""

        old_container = self._container_by_id(start_container_id)
        target_container = self._container_at_node(operator)

        old_container_id = _item_id(old_container)
        target_container_id = _item_id(target_container)

        if old_container_id and old_container_id == target_container_id:
            return True, ""
        current_service_id = ""
        try:
            current_service_id = str(operator.svcId or "").strip()
        except (AttributeError, RuntimeError, TypeError):
            current_service_id = ""
        if current_service_id and current_service_id == target_container_id:
            return True, ""

        sx, sy = float(start_pos[0]), float(start_pos[1])
        if target_container is None:
            self._set_node_scene_pos(operator, x=sx, y=sy)
            msg = "Operator nodes must be dropped inside a compatible service container."
            show_warning(self._notification_parent(), "Move Operator Failed", msg)
            return False, msg

        target_service_class = str(target_container.spec.serviceClass or "")
        if target_service_class != service_class:
            self._set_node_scene_pos(operator, x=sx, y=sy)
            msg = (
                "Operator serviceClass does not match target container serviceClass. "
                f"operator={service_class}, container={target_service_class}"
            )
            show_warning(self._notification_parent(), "Move Operator Failed", msg)
            return False, msg

        operators_to_move = self._selected_operators_for_same_container_drop(
            anchor_operator=operator,
            target_container=target_container,
        )

        self._unbind_operator_from_container(operator=operator, container=old_container)
        if not self._bind_operator_to_container(operator, target_container):
            self._set_node_scene_pos(operator, x=sx, y=sy)
            if old_container is not None:
                self._bind_operator_to_container(operator, old_container)
            msg = "Failed to bind operator to target service container."
            show_warning(self._notification_parent(), "Move Operator Failed", msg)
            return False, msg

        bound_operators: list[_BASE_OPERATOR_CLS_] = [operator]
        for peer_operator in operators_to_move:
            if peer_operator is operator:
                continue
            peer_old_container = self._operator_current_container(peer_operator)
            self._unbind_operator_from_container(operator=peer_operator, container=peer_old_container)
            if self._bind_operator_to_container(peer_operator, target_container):
                bound_operators.append(peer_operator)
                continue
            if peer_old_container is not None:
                self._bind_operator_to_container(peer_operator, peer_old_container)
            logger.warning(
                "Failed to bind selected operator %s to target service container %s.",
                _item_id(peer_operator),
                target_container_id,
            )

        dropped_count = 0
        for moved_operator in bound_operators:
            dropped_count += self._disconnect_invalid_connections_for_operator(moved_operator)
        if dropped_count > 0:
            show_warning(
                self._notification_parent(),
                "Operator Moved",
                f"Moved operator to service `{target_container_id}` and dropped {dropped_count} invalid connection(s).",
            )
        return True, ""

    def _ensure_operator_in_container(
        self,
        node: Any,
        *,
        pos: list[float] | tuple[float, float] | None,
    ) -> tuple[bool, str | None]:
        """
        Enforce:
        - operator nodes must be placed within a service container (unless canvas-managed)
        - bind `svcId` and container child relationship

        Returns (ok, message). If ok is False, caller should not keep/add the node.
        """
        if not self._is_operator_node(node):
            return True, None

        if node.spec.serviceClass == _CANVAS_SERVICE_CLASS_:
            node.svcId = STUDIO_SERVICE_ID  # type: ignore[attr-defined]
            try:
                if "svcId" in node.model.properties or "svcId" in node.model.custom_properties:
                    node.set_property("svcId", STUDIO_SERVICE_ID, push_undo=False)
            except (AttributeError, RuntimeError, TypeError):
                pass
            return True, None

        in_scene = node.view.scene() is not None
        node_rect = _scene_rect(node) if in_scene else _rect_at_pos(node.view, pos or node.model.pos)

        container = self._container_at_rect(node_rect)
        if container is None:
            return False, "Operator nodes must be placed within a service container."

        if not self._bind_operator_to_container(node, container):
            return False, "Operator nodes must be placed within a compatible service container."

        return True, None

    def _on_nodes_deleted(self, node_ids: list[str]) -> None:
        """
        Keep container child lists clean when nodes are deleted (including undo).
        """
        if not node_ids:
            return
        dead = set(node_ids)
        for container in self.all_nodes():
            if not self._is_container_node(container):
                continue
            container._child_nodes = [n for n in container._child_nodes if n.id not in dead]

            kept = []
            for view in container.view._child_views:
                vid = view.id
                if vid in dead:
                    view._container_item = None
                    continue
                kept.append(view)
            container.view._child_views = kept

    def _rebind_container_children(self) -> None:
        """
        Rebuild container -> operator bindings from geometry.

        This is used after session load to restore:
        - container dragging moves operators
        - operator drag clamping (via `view._container_item`)
        """
        containers: list[_BASE_CONTAINER_CLS_] = []
        operators: list[BaseNode] = []
        for node in self.all_nodes():
            if self._is_container_node(node):
                containers.append(node)
            elif self._is_operator_node(node):
                operators.append(node)

        # Clear container child lists.
        for container in containers:
            container._child_nodes = []
            container.view._child_views = []

        # Clear operator back-references.
        for op in operators:
            op.view._container_item = None

        # Rebind operators based on intersecting container geometry.
        for op in operators:
            if op.spec.serviceClass == _CANVAS_SERVICE_CLASS_:
                # Studio (editor-local) operators belong to the built-in PyStudio service.
                # They are not bound to a container instance, but still need a stable svcId.
                op.svcId = STUDIO_SERVICE_ID  # type: ignore[attr-defined]
                try:
                    if "svcId" in op.model.properties or "svcId" in op.model.custom_properties:
                        op.set_property("svcId", STUDIO_SERVICE_ID, push_undo=False)
                except (AttributeError, RuntimeError, TypeError):
                    pass
                continue

            container = self._container_at_node(op)
            if container is None:
                # Leave as orphan so user can fix placement manually.
                op.svcId = ""  # type: ignore[attr-defined]
                try:
                    if "svcId" in op.model.properties or "svcId" in op.model.custom_properties:
                        op.set_property("svcId", "", push_undo=False)
                except (AttributeError, RuntimeError, TypeError):
                    pass
                logger.warning('Operator "%s" is not inside any container after load.', op.name())
                continue

            self._bind_operator_to_container(op, container)
