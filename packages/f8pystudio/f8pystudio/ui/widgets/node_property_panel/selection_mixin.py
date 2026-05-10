from __future__ import annotations

import time
from typing import Any, cast

from qtpy import QtCore

from ....nodegraph.node_base import F8StudioBaseNode


class NodePropertyPanelSelectionMixin:
    def _wire_graph_signals(self) -> None:
        host = cast(Any, self)
        g = host._node_graph
        g.node_selected.connect(host._on_node_selected)  # type: ignore[attr-defined]
        g.node_double_clicked.connect(host._on_node_selected)  # type: ignore[attr-defined]
        g.node_selection_changed.connect(host._on_node_selection_changed)  # type: ignore[attr-defined]
        g.nodes_deleted.connect(host._on_nodes_deleted)  # type: ignore[attr-defined]
        g.property_changed.connect(host._on_graph_property_changed)  # type: ignore[attr-defined]
        g.layers_changed.connect(host._on_graph_layers_changed)  # type: ignore[attr-defined]
        g.port_connected.connect(host._on_graph_ports_changed)  # type: ignore[attr-defined]
        g.port_disconnected.connect(host._on_graph_ports_changed)  # type: ignore[attr-defined]

    def _clear_editor(self, *, clear_node_id: bool = True) -> None:
        host = cast(Any, self)
        if clear_node_id:
            host._node_id = None
        editor = host._editor
        if editor is not None:
            host._editor = None
            host._container_layout.removeWidget(editor)
            try:
                editor.setVisible(False)
            except (AttributeError, RuntimeError, TypeError):
                host._log_exception("Failed to hide editor before deleteLater")
            editor.deleteLater()
        try:
            host._empty.setVisible(True)
        except (AttributeError, RuntimeError, TypeError):
            host._log_exception("Failed to show empty editor placeholder")

    def _set_editor(self, editor: Any) -> None:
        host = cast(Any, self)
        host._clear_editor(clear_node_id=False)
        host._editor = editor
        try:
            host._empty.setVisible(False)
        except (AttributeError, RuntimeError, TypeError):
            host._log_exception("Failed to hide empty editor placeholder")
        host._container_layout.addWidget(editor, 0)
        host._sync_container_width()
        try:
            if not host._inspect_mode:
                editor.property_changed.connect(host._on_editor_property_changed)  # type: ignore[attr-defined]
        except (AttributeError, RuntimeError, TypeError):
            host._log_exception("Failed to connect editor.property_changed")
        try:
            if not host._inspect_mode:
                editor.property_changing.connect(host._on_editor_property_changing)  # type: ignore[attr-defined]
        except (AttributeError, RuntimeError, TypeError):
            host._log_exception("Failed to connect editor.property_changing")
        try:
            editor.property_closed.connect(host._on_editor_closed)  # type: ignore[attr-defined]
        except (AttributeError, RuntimeError, TypeError):
            host._log_exception("Failed to connect editor.property_closed")

    def _restore_outer_scroll_position(self, value: int) -> None:
        host = cast(Any, self)
        try:
            host._scroll.verticalScrollBar().setValue(int(value))
        except (AttributeError, RuntimeError, TypeError, ValueError):
            host._log_exception("Failed to restore property panel scroll position")

    def set_node(self, node: F8StudioBaseNode | None, *, force_clear: bool = False) -> None:
        host = cast(Any, self)
        if node is None:
            if force_clear or host._editor is None:
                host._clear_editor(clear_node_id=True)
            return
        node_id = str(node.id or "")
        if not node_id:
            host._clear_editor(clear_node_id=True)
            return
        if host._node_id == node_id and host._editor is not None:
            return
        previous_editor = host._editor
        previous_view_state = previous_editor.snapshot_view_state() if previous_editor is not None else None
        try:
            previous_outer_scroll = int(host._scroll.verticalScrollBar().value())
        except (AttributeError, RuntimeError, TypeError, ValueError):
            previous_outer_scroll = 0
        host._node_id = node_id
        host._last_ui_overrides_reload_fingerprint = host._ui_overrides_reload_fingerprint_from_node(node)
        editor = host._build_property_editor(node=node)
        host._set_editor(editor)
        restored_same_tab = editor.restore_view_state(previous_view_state)
        if restored_same_tab:
            QtCore.QTimer.singleShot(0, lambda value=previous_outer_scroll: host._restore_outer_scroll_position(value))
            return
        host._restore_outer_scroll_position(0)

    def _on_node_selected(self, node: Any) -> None:
        host = cast(Any, self)
        host._last_node_click_ts = time.monotonic()
        host.set_node(node)

    def _on_node_selection_changed(self, selected: list[Any], _deselected: list[Any]) -> None:
        host = cast(Any, self)
        try:
            host._selection_timer.start(0)
        except Exception:
            if selected:
                host.set_node(selected[0])

    def _on_nodes_deleted(self, node_ids: list[str]) -> None:
        host = cast(Any, self)
        if not host._node_id:
            return
        if host._node_id in set(str(x) for x in (node_ids or [])):
            host.set_node(None, force_clear=True)

    def _on_editor_closed(self, _node_id: str) -> None:
        cast(Any, self).set_node(None, force_clear=True)

    def _apply_graph_selection(self) -> None:
        host = cast(Any, self)
        selected_nodes = list(host._node_graph.selected_nodes() or [])
        if selected_nodes:
            host.set_node(selected_nodes[0])
