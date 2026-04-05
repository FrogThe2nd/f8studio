from __future__ import annotations

import logging
from typing import Callable, cast

from NodeGraphQt.errors import NodeCreationError
from NodeGraphQt.nodes.base_node import NodeBaseWidget
from qtpy import QtCore, QtGui, QtWidgets

from f8pysdk import F8VariantRecord
from f8pysdk.msgspec_codec import dump_json

from ...assets.common import JsonObject, json_object_from_value
from ...nodegraph.node_graph import F8StudioGraph
from ...nodegraph.viewer import F8StudioNodeViewer
from ..variants.variant_ids import build_variant_node_type

logger = logging.getLogger(__name__)

_DEFAULT_EMPTY_MESSAGE = "Select an asset to preview."
_PREVIEW_BUILD_DELAY_MS = 24
_PREVIEW_FIT_RETRY_DELAYS_MS = (24, 80, 160)
_LOADING_MESSAGE = "Building preview..."


class _AssetPreviewViewer(F8StudioNodeViewer):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent=parent)
        self.setViewportUpdateMode(QtWidgets.QGraphicsView.BoundingRectViewportUpdate)
        self.setCacheMode(QtWidgets.QGraphicsView.CacheBackground)
        self._shortcut_search.setEnabled(False)
        self._shortcut_delete.setEnabled(False)
        self._shortcut_backspace.setEnabled(False)
        self.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.NoContextMenu)
        self._inline_editor_refresh_timer.stop()

    def _wheel_over_text_input_proxy(self, event: QtGui.QWheelEvent) -> bool:  # type: ignore[name-defined]
        _ = event
        return False

    def _delete_selected_nodes(self) -> None:
        return

    def _open_node_search(self) -> None:
        return

    def contextMenuEvent(self, event) -> None:  # type: ignore[override]
        event.accept()

    def sceneMousePressEvent(self, event) -> None:  # type: ignore[override]
        button = event.button()
        if button in (
            QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.MouseButton.RightButton,
        ):
            event.accept()
            return
        super().sceneMousePressEvent(event)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        button = event.button()
        if button in (
            QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.MouseButton.RightButton,
        ):
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        button = event.button()
        if button in (
            QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.MouseButton.RightButton,
        ):
            event.accept()
            return
        super().mouseReleaseEvent(event)


class AssetGraphPreviewPane(QtWidgets.QWidget):
    def __init__(
        self,
        *,
        parent: QtWidgets.QWidget | None,
        host_graph: object | None,
    ) -> None:
        super().__init__(parent)
        self._host_graph = host_graph
        self._viewer = _AssetPreviewViewer(self)
        self._preview_graph = F8StudioGraph(parent=self, viewer=self._viewer)
        self._preview_graph._skip_post_load_viewer_refresh = True  # pyright: ignore[reportAttributeAccessIssue]
        self._status = QtWidgets.QLabel(self)
        self._status.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._status.setWordWrap(True)
        self._status.setMargin(24)
        self._loading_page = QtWidgets.QWidget(self)
        self._loading_label = QtWidgets.QLabel(self._loading_page)
        self._loading_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._loading_label.setWordWrap(False)
        self._loading_label.setMargin(16)
        self._loading_label.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        loading_font = self._loading_label.font()
        loading_font.setPointSize(max(10, int(loading_font.pointSize())))
        self._loading_label.setFont(loading_font)
        self._loading_label.setMinimumHeight(
            max(40, self._loading_label.fontMetrics().height() + 20),
        )
        loading_layout = QtWidgets.QVBoxLayout(self._loading_page)
        loading_layout.setContentsMargins(24, 24, 24, 24)
        loading_layout.addStretch(1)
        loading_layout.addWidget(self._loading_label, 0)
        loading_layout.addStretch(1)
        self._stack = QtWidgets.QStackedLayout()
        self._stack.addWidget(self._preview_graph.widget)
        self._stack.addWidget(self._loading_page)
        self._stack.addWidget(self._status)
        self._preview_request_timer = QtCore.QTimer(self)
        self._preview_request_timer.setSingleShot(True)
        self._preview_request_timer.timeout.connect(self._flush_pending_preview_request)  # type: ignore[attr-defined]
        self._pending_component_payload: JsonObject | None = None
        self._pending_variant_record: F8VariantRecord | None = None
        self._pending_request_kind: str | None = None
        self._pending_request_id = 0
        self._current_request_id = 0
        self._node_factory_signature: tuple[tuple[str, int], ...] = ()

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self._stack)

        self.clear_preview()

    @property
    def preview_graph(self) -> F8StudioGraph:
        return self._preview_graph

    def current_status_text(self) -> str:
        if self._stack.currentWidget() is self._loading_page:
            return str(self._loading_label.text() or "")
        return str(self._status.text() or "")

    def clear_preview(self, message: str = _DEFAULT_EMPTY_MESSAGE) -> None:
        self._cancel_pending_preview_request()
        self._clear_graph()
        self._show_status(message)

    def show_component_payload(self, payload: JsonObject) -> None:
        request_id = self._next_request_id()
        self._pending_component_payload = payload
        self._pending_variant_record = None
        self._pending_request_kind = "component"
        self._pending_request_id = request_id
        self._show_loading(_LOADING_MESSAGE)
        self._preview_request_timer.start(_PREVIEW_BUILD_DELAY_MS)

    def show_variant_record(self, record: F8VariantRecord) -> None:
        request_id = self._next_request_id()
        self._pending_component_payload = None
        self._pending_variant_record = record
        self._pending_request_kind = "variant"
        self._pending_request_id = request_id
        self._show_loading(_LOADING_MESSAGE)
        self._preview_request_timer.start(_PREVIEW_BUILD_DELAY_MS)

    def _flush_pending_preview_request(self) -> None:
        request_kind = self._pending_request_kind
        component_payload = self._pending_component_payload
        variant_record = self._pending_variant_record
        request_id = int(self._pending_request_id)
        self._pending_request_kind = None
        self._pending_component_payload = None
        self._pending_variant_record = None
        self._pending_request_id = 0
        if request_kind == "component" and component_payload is not None:
            self._render_component_payload(component_payload, request_id=request_id)
            return
        if request_kind == "variant" and variant_record is not None:
            self._render_variant_record(variant_record, request_id=request_id)
            return

    def _render_component_payload(self, payload: JsonObject, *, request_id: int) -> None:
        if request_id != self._current_request_id:
            return
        if not self._sync_registered_nodes():
            self.clear_preview("Preview unavailable without an editor graph.")
            return
        try:
            self._run_with_preview_updates_frozen(lambda: self._preview_graph.load_session_payload(payload))
        except Exception as exc:
            self._clear_graph()
            logger.exception("Failed to render component preview.")
            self._show_status(f"Failed to preview component.\n{exc}")
            return
        self._finalize_loaded_preview(request_id=request_id)

    def _render_variant_record(self, record: F8VariantRecord, *, request_id: int) -> None:
        if request_id != self._current_request_id:
            return
        if not self._sync_registered_nodes():
            self.clear_preview("Preview unavailable without an editor graph.")
            return
        try:
            self._run_with_preview_updates_frozen(lambda: self._render_variant_record_inner(record))
        except Exception as exc:
            self._clear_graph()
            logger.exception("Failed to render variant preview variant_id=%s", str(record.variantId))
            self._show_status(f"Failed to preview variant.\n{exc}")
            return
        self._finalize_loaded_preview(request_id=request_id)

    def _render_variant_record_inner(self, record: F8VariantRecord) -> None:
        self._clear_graph()
        _ = self._create_variant_preview_node(record)

    def _sync_registered_nodes(self) -> bool:
        host_graph = self._host_graph
        if not isinstance(host_graph, F8StudioGraph):
            return False
        host_nodes = list(host_graph.node_factory.nodes.items())
        signature = tuple((str(node_type), id(node_cls)) for node_type, node_cls in host_nodes)
        preview_factory = self._preview_graph.node_factory
        if signature != self._node_factory_signature:
            preview_factory.clear_registered_nodes()
            for _node_type, node_cls in host_nodes:
                preview_factory.register_node(node_cls)
            self._node_factory_signature = signature
        return bool(preview_factory.nodes)

    def _clear_graph(self) -> None:
        try:
            self._preview_graph.clear_session()
        except Exception:
            logger.exception("Failed to clear preview graph.")
        self._preview_graph._undo_stack.clear()

    def _show_status(self, message: str) -> None:
        self._status.setText(str(message or _DEFAULT_EMPTY_MESSAGE))
        self._stack.setCurrentWidget(self._status)

    def _show_loading(self, message: str) -> None:
        self._loading_label.setText(str(message or _LOADING_MESSAGE))
        self._stack.setCurrentWidget(self._loading_page)

    def _show_graph(self) -> None:
        self._stack.setCurrentWidget(self._preview_graph.widget)

    def _run_with_preview_updates_frozen(self, callback: Callable[[], None]) -> None:
        widgets_to_freeze: list[QtWidgets.QWidget] = []
        graph_widget = getattr(self._preview_graph, "widget", None)
        viewer = self._preview_graph.viewer()
        if isinstance(graph_widget, QtWidgets.QWidget):
            widgets_to_freeze.append(graph_widget)
        if isinstance(viewer, QtWidgets.QWidget) and viewer is not graph_widget:
            widgets_to_freeze.append(viewer)
        for widget in widgets_to_freeze:
            widget.setUpdatesEnabled(False)
        try:
            callback()
        finally:
            for widget in reversed(widgets_to_freeze):
                widget.setUpdatesEnabled(True)
                widget.update()

    def _cancel_pending_preview_request(self) -> None:
        self._current_request_id = self._next_request_id()
        self._pending_request_kind = None
        self._pending_component_payload = None
        self._pending_variant_record = None
        self._pending_request_id = 0
        if self._preview_request_timer.isActive():
            self._preview_request_timer.stop()

    def _finalize_loaded_preview(self, *, request_id: int) -> None:
        if request_id != self._current_request_id:
            return
        self._lock_preview_content()
        self._clear_selected_nodes()
        self._show_graph()
        self._schedule_focus_loaded_nodes(request_id=request_id)

    def _schedule_focus_loaded_nodes(self, *, request_id: int) -> None:
        if request_id != self._current_request_id:
            return
        self._focus_loaded_nodes()
        for delay_ms in _PREVIEW_FIT_RETRY_DELAYS_MS:
            QtCore.QTimer.singleShot(
                delay_ms,
                lambda request_id=request_id: self._focus_loaded_nodes_if_current(request_id),
            )

    def _focus_loaded_nodes_if_current(self, request_id: int) -> None:
        if request_id != self._current_request_id:
            return
        self._focus_loaded_nodes()

    def _next_request_id(self) -> int:
        self._current_request_id += 1
        return int(self._current_request_id)

    def _create_variant_preview_node(self, record: F8VariantRecord) -> object:
        variant_node_type = build_variant_node_type(str(record.variantId))
        try:
            node = self._create_preview_node(node_type=variant_node_type, name=str(record.name or "").strip() or None)
            if node is not None:
                return node
        except NodeCreationError:
            logger.debug(
                "Preview variant node creation fell back to direct base node application variant_id=%s",
                str(record.variantId),
            )

        base_node_type = str(record.baseNodeType or "").strip()
        if not base_node_type:
            raise ValueError("Variant preview requires a non-empty baseNodeType.")
        node = self._create_preview_node(node_type=base_node_type, name=str(record.name or "").strip() or None)
        if node is None:
            raise ValueError(f'Unable to create preview node for "{base_node_type}".')
        variant_spec_json = record.spec if isinstance(record.spec, dict) else dump_json(record.spec, mode="json")
        self._preview_graph._apply_variant_to_node(  # pyright: ignore[reportPrivateUsage]
            node=cast(object, node),
            variant_record=record,
            variant_spec_json=json_object_from_value(variant_spec_json),
        )
        return node

    def _create_preview_node(self, *, node_type: str, name: str | None) -> object | None:
        previous_loading = bool(self._preview_graph._loading_session)
        self._preview_graph._loading_session = True
        try:
            return self._preview_graph.create_node(
                node_type,
                name=name,
                selected=False,
                pos=(0.0, 0.0),
                push_undo=False,
            )
        finally:
            self._preview_graph._loading_session = previous_loading

    def _clear_selected_nodes(self) -> None:
        self._preview_graph.clear_selection()
        for node in list(self._preview_graph.all_nodes() or []):
            try:
                node.set_property("selected", False, push_undo=False)
            except (AttributeError, RuntimeError, TypeError):
                continue

    def _focus_loaded_nodes(self) -> None:
        viewer = self._preview_graph.viewer()
        if not isinstance(viewer, QtWidgets.QGraphicsView):
            return
        scene = viewer.scene()
        if scene is None:
            return
        node_rects: list[QtCore.QRectF] = []
        for node in list(self._preview_graph.all_nodes() or []):
            try:
                rect = self._item_scene_rect(node.view)
            except (AttributeError, RuntimeError, TypeError):
                continue
            if rect.isNull() or not rect.isValid():
                continue
            node_rects.append(rect)
        scene_rect = scene.itemsBoundingRect()
        for rect in node_rects:
            if scene_rect.isNull() or not scene_rect.isValid():
                scene_rect = QtCore.QRectF(rect)
                continue
            scene_rect = scene_rect.united(rect)
        if scene_rect.isNull() or not scene_rect.isValid():
            return
        padding_x = 56.0
        padding_y = 96.0
        padded_rect = QtCore.QRectF(scene_rect)
        padded_rect.adjust(-padding_x, -padding_y, padding_x, padding_y)
        if isinstance(viewer, F8StudioNodeViewer):
            viewer.set_view_range_rect(padded_rect)
        else:
            viewer.fitInView(padded_rect, QtCore.Qt.AspectRatioMode.KeepAspectRatio)
            viewer.centerOn(padded_rect.center())
            viewer.viewport().update()

    @staticmethod
    def _item_scene_rect(item: QtWidgets.QGraphicsItem) -> QtCore.QRectF:
        base_rect = QtCore.QRectF()
        try:
            base_rect = item.boundingRect()
        except (AttributeError, RuntimeError, TypeError):
            base_rect = QtCore.QRectF()
        try:
            child_rect = item.childrenBoundingRect()
        except (AttributeError, RuntimeError, TypeError):
            child_rect = QtCore.QRectF()
        combined_rect = QtCore.QRectF(base_rect)
        if child_rect.isValid() and not child_rect.isNull():
            if combined_rect.isValid() and not combined_rect.isNull():
                combined_rect = combined_rect.united(child_rect)
            else:
                combined_rect = QtCore.QRectF(child_rect)
        if combined_rect.isValid() and not combined_rect.isNull():
            try:
                return item.mapToScene(combined_rect).boundingRect()
            except (AttributeError, RuntimeError, TypeError):
                pass
        try:
            return item.sceneBoundingRect()
        except (AttributeError, RuntimeError, TypeError):
            return QtCore.QRectF()

    def _lock_preview_content(self) -> None:
        scene = self._viewer.scene()
        if scene is None:
            return
        for item in list(scene.items()):
            if not isinstance(item, QtWidgets.QGraphicsProxyWidget):
                continue
            widget = item.widget()
            if widget is None:
                continue
            self._lock_widget_tree(widget)

    def _lock_widget_tree(self, widget: QtWidgets.QWidget) -> None:
        self._apply_read_only_to_widget(widget)
        for child in widget.findChildren(QtWidgets.QWidget):
            self._apply_read_only_to_widget(child)

    @staticmethod
    def _apply_read_only_to_widget(widget: QtWidgets.QWidget) -> None:
        widget.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        widget.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.NoContextMenu)
        if isinstance(widget, QtWidgets.QLineEdit):
            widget.setReadOnly(True)
            return
        if isinstance(widget, QtWidgets.QPlainTextEdit):
            widget.setReadOnly(True)
            return
        if isinstance(widget, QtWidgets.QTextEdit):
            widget.setReadOnly(True)
            widget.setTextInteractionFlags(
                QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
                | QtCore.Qt.TextInteractionFlag.TextSelectableByKeyboard
            )
            return
        if isinstance(widget, QtWidgets.QAbstractSpinBox):
            widget.setReadOnly(True)
            widget.setButtonSymbols(QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons)
            return
        if isinstance(widget, NodeBaseWidget):
            group_widget = widget.widget()
            if isinstance(group_widget, QtWidgets.QWidget):
                group_widget.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)


__all__ = ["AssetGraphPreviewPane"]
