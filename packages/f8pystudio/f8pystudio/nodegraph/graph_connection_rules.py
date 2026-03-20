from __future__ import annotations

import logging
from typing import Any

from NodeGraphQt.base.port import Port as NGPort
from NodeGraphQt.constants import PortTypeEnum

from .edge_rules import normalize_edge_kind, port_view_name, validate_runtime_connection
from .service_basenode import F8StudioServiceNodeItem
from .viewer import F8StudioNodeViewer

logger = logging.getLogger(__name__)


class GraphConnectionRulesMixin:
    def _on_port_connected(self, in_port: NGPort, out_port: NGPort) -> None:
        self._on_port_connection_changed(in_port=in_port, out_port=out_port)

    def _on_port_disconnected(self, in_port: NGPort, out_port: NGPort) -> None:
        self._on_port_connection_changed(in_port=in_port, out_port=out_port)

    def set_edge_kind_visible(self, kind: str, visible: bool) -> None:
        normalized = normalize_edge_kind(kind)
        if normalized is None:
            raise ValueError(f"unknown edge kind: {kind}")
        viewer = self._viewer
        if not isinstance(viewer, F8StudioNodeViewer):
            return
        viewer.set_edge_kind_visible(normalized, bool(visible))

    def edge_kind_visible(self, kind: str) -> bool:
        normalized = normalize_edge_kind(kind)
        if normalized is None:
            raise ValueError(f"unknown edge kind: {kind}")
        viewer = self._viewer
        if not isinstance(viewer, F8StudioNodeViewer):
            return True
        return bool(viewer.edge_kind_visible(normalized))

    @staticmethod
    def _ordered_port_views(port_a: Any, port_b: Any) -> tuple[Any, Any] | None:
        if port_a.port_type == PortTypeEnum.OUT.value and port_b.port_type == PortTypeEnum.IN.value:
            return port_a, port_b
        if port_b.port_type == PortTypeEnum.OUT.value and port_a.port_type == PortTypeEnum.IN.value:
            return port_b, port_a
        return None

    def _connection_views_allowed(self, port_a: Any, port_b: Any) -> tuple[bool, str]:
        ordered = self._ordered_port_views(port_a, port_b)
        if ordered is None:
            return False, "connection must be between output and input ports"
        out_view, in_view = ordered
        out_node_id = str(out_view.node.id or "").strip()
        in_node_id = str(in_view.node.id or "").strip()
        if not out_node_id or not in_node_id:
            return False, "connection endpoints are missing node ids"

        try:
            out_node = self.get_node_by_id(out_node_id)
            in_node = self.get_node_by_id(in_node_id)
        except (AttributeError, KeyError, RuntimeError, TypeError):
            return False, "connection endpoint nodes not found"
        if out_node is None or in_node is None:
            return False, "connection endpoint nodes not found"

        return validate_runtime_connection(
            out_port_name=port_view_name(out_view),
            in_port_name=port_view_name(in_view),
            out_node=out_node,
            in_node=in_node,
        )

    def _on_connection_changed(self, disconnected, connected):  # type: ignore[override]
        if not (disconnected or connected):
            return

        valid_connected = []
        rejected_count = 0
        for pair in list(connected or []):
            if not (isinstance(pair, (list, tuple)) and len(pair) == 2):
                rejected_count += 1
                continue
            allowed, reason = self._connection_views_allowed(pair[0], pair[1])
            if not allowed:
                rejected_count += 1
                logger.warning("Rejected invalid connection: %s", reason)
                continue
            valid_connected.append(pair)

        if rejected_count:
            logger.warning("Rejected %s invalid connection(s) by studio edge rules.", rejected_count)

        valid_disconnected = list(disconnected or [])
        if list(connected or []):
            if not valid_connected:
                valid_disconnected = []
            else:
                endpoints: set[Any] = set()
                for a, b in valid_connected:
                    endpoints.add(a)
                    endpoints.add(b)
                filtered = []
                for pair in list(disconnected or []):
                    if not (isinstance(pair, (list, tuple)) and len(pair) == 2):
                        continue
                    if pair[0] in endpoints or pair[1] in endpoints:
                        filtered.append(pair)
                valid_disconnected = filtered
        super()._on_connection_changed(valid_disconnected, valid_connected)

    @staticmethod
    def _is_state_port(port: NGPort) -> bool:
        name = str(port.name() or "")
        return name.startswith("[S]") or name.endswith("[S]")

    def _on_port_connection_changed(self, *, in_port: NGPort, out_port: NGPort) -> None:
        """
        Refresh inline state read-only state when state edges are connected/disconnected.

        When a state field is upstream-bound via a state edge, Studio should treat it as
        read-only in the node UI (inline controls).
        """
        if not (self._is_state_port(in_port) or self._is_state_port(out_port)):
            return

        for p in (in_port, out_port):
            if not self._is_state_port(p):
                continue
            node = p.node()
            view = node.view
            if not isinstance(view, F8StudioServiceNodeItem):
                continue
            view.refresh_state_inline_control_read_only()
            view.update()
