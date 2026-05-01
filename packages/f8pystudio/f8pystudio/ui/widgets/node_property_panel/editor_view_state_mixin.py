from __future__ import annotations

import logging
import weakref
from typing import Any, cast

from qtpy import QtCore, QtWidgets

from ...support.node_property_support import node_missing_lock_info
from ...support.qt_lifecycle import qt_object_is_valid, qt_runtime_error_is_object_deleted
from .editor_tabs_mixin import NodePropertyEditorTabsMixin

logger = logging.getLogger(__name__)


class NodePropertyEditorViewStateMixin(NodePropertyEditorTabsMixin):
    @staticmethod
    def _restore_tab_scroll_positions(
        tab_widget: QtWidgets.QTabWidget,
        tab_scroll_positions: dict[str, int],
    ) -> None:
        if not qt_object_is_valid(tab_widget):
            return
        try:
            tab_count = tab_widget.count()
        except RuntimeError as exc:
            if qt_runtime_error_is_object_deleted(exc):
                return
            logger.exception("Failed to inspect property editor tab count")
            return
        for index in range(tab_count):
            try:
                tab_name = tab_widget.tabText(index)
                widget = tab_widget.widget(index)
            except RuntimeError as exc:
                if qt_runtime_error_is_object_deleted(exc):
                    return
                logger.exception("Failed to inspect property editor tab index=%s", index)
                continue
            if tab_name not in tab_scroll_positions:
                continue
            if widget is None or not qt_object_is_valid(widget):
                continue
            try:
                areas = widget.findChildren(QtWidgets.QScrollArea)
            except RuntimeError as exc:
                if qt_runtime_error_is_object_deleted(exc):
                    continue
                logger.exception("Failed to inspect property editor tab scroll areas tab=%s", tab_name)
                continue
            if not areas:
                continue
            area = areas[0]
            if not qt_object_is_valid(area):
                continue
            try:
                scrollbar = area.verticalScrollBar()
                if not qt_object_is_valid(scrollbar):
                    continue
                scrollbar.setValue(int(tab_scroll_positions[tab_name]))
            except (AttributeError, RuntimeError, TypeError, ValueError):
                logger.exception("Failed to restore property editor tab scroll position tab=%s", tab_name)
                continue

    def _on_spec_applied(self) -> None:
        host = cast(Any, self)
        node = host._node
        if node is None:
            return
        try:
            node.sync_from_spec()
        except Exception:
            logger.exception("sync_from_spec failed before reload")
        host.reload()

    def reload(self) -> None:
        host = cast(Any, self)
        if host._reload_pending:
            return
        host._reload_pending = True
        host._reload_timer.start(int(host._reload_debounce_ms))

    def snapshot_view_state(self):
        host = cast(Any, self)
        tab_widget = self._tab_widget(host)
        current_tab: str | None = None
        current_index = tab_widget.currentIndex()
        if current_index >= 0:
            current_tab = tab_widget.tabText(current_index)
        scroll_positions: dict[str, int] = {}
        for index in range(tab_widget.count()):
            tab_name = tab_widget.tabText(index)
            widget = tab_widget.widget(index)
            if widget is None:
                continue
            areas = widget.findChildren(QtWidgets.QScrollArea)
            if not areas:
                continue
            try:
                scroll_positions[tab_name] = int(areas[0].verticalScrollBar().value())
            except (AttributeError, RuntimeError, TypeError, ValueError):
                continue
        return host._VIEW_STATE_CLS(
            current_tab=current_tab,
            tab_scroll_positions=scroll_positions,
        )

    def restore_view_state(self, state: Any | None) -> bool:
        host = cast(Any, self)
        if state is None:
            return False
        tab_widget = self._tab_widget(host)
        restored_current_tab = False
        try:
            current_tab = state.current_tab
        except AttributeError:
            current_tab = None
        if current_tab:
            for index in range(tab_widget.count()):
                if tab_widget.tabText(index) != current_tab:
                    continue
                tab_widget.setCurrentIndex(index)
                restored_current_tab = True
                break
        try:
            tab_scroll_positions = state.tab_scroll_positions
        except AttributeError:
            tab_scroll_positions = None
        if tab_scroll_positions:
            tab_widget_ref = weakref.ref(tab_widget)
            positions = dict(tab_scroll_positions)

            def _restore() -> None:
                restored_tab_widget = tab_widget_ref()
                if restored_tab_widget is None:
                    return
                NodePropertyEditorViewStateMixin._restore_tab_scroll_positions(restored_tab_widget, positions)

            QtCore.QTimer.singleShot(0, _restore)
            QtCore.QTimer.singleShot(0, lambda: QtCore.QTimer.singleShot(0, _restore))
            QtCore.QTimer.singleShot(50, _restore)
        return restored_current_tab

    def _clear_tabs(self) -> None:
        host = cast(Any, self)
        tab_widget = self._tab_widget(host)
        while tab_widget.count():
            widget = tab_widget.widget(0)
            tab_widget.removeTab(0)
            if widget is None:
                continue
            try:
                widget.setVisible(False)
            except Exception:
                logger.exception("Failed to hide tab widget before deleteLater")
            widget.deleteLater()

    def _reload_now(self) -> None:
        host = cast(Any, self)
        host._reload_pending = False
        node = host._node
        if node is None:
            return
        previous_view_state = host.snapshot_view_state()
        previous_outer_scroll = host.snapshot_outer_scroll_position()
        host._clear_tabs()
        host._F8StudioNodePropEditorWidget__tab_windows = {}
        host._option_pool_dependents = {}
        host._port_connections = host._read_node(node)
        missing_locked, missing_type = node_missing_lock_info(node)
        try:
            host._missing_banner.setVisible(bool(missing_locked))
            if missing_locked:
                host._missing_banner.setText(f"Missing dependency: {missing_type or 'unknown type'}")
        except (AttributeError, RuntimeError, TypeError):
            logger.exception("Failed to update missing banner")
        if missing_locked:
            host._apply_missing_lock_read_only()
        host.restore_view_state(previous_view_state)
        host.restore_outer_scroll_position_later(previous_outer_scroll)
