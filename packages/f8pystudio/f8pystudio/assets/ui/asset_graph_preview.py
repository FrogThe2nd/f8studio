from __future__ import annotations

import logging
from typing import cast

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


class _AssetPreviewViewer(F8StudioNodeViewer):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent=parent)
        self._shortcut_search.setEnabled(False)
        self._shortcut_delete.setEnabled(False)
        self._shortcut_backspace.setEnabled(False)
        self.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.NoContextMenu)

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
        self._status = QtWidgets.QLabel(self)
        self._status.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._status.setWordWrap(True)
        self._status.setMargin(24)
        self._stack = QtWidgets.QStackedLayout()
        self._stack.addWidget(self._preview_graph.widget)
        self._stack.addWidget(self._status)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self._stack)

        self.clear_preview()

    @property
    def preview_graph(self) -> F8StudioGraph:
        return self._preview_graph

    def current_status_text(self) -> str:
        return str(self._status.text() or "")

    def clear_preview(self, message: str = _DEFAULT_EMPTY_MESSAGE) -> None:
        self._clear_graph()
        self._show_status(message)

    def show_component_payload(self, payload: JsonObject) -> None:
        if not self._sync_registered_nodes():
            self.clear_preview("Preview unavailable without an editor graph.")
            return
        try:
            self._preview_graph.load_session_payload(payload)
        except Exception as exc:
            self._clear_graph()
            logger.exception("Failed to render component preview.")
            self._show_status(f"Failed to preview component.\n{exc}")
            return
        self._finalize_loaded_preview()

    def show_variant_record(self, record: F8VariantRecord) -> None:
        if not self._sync_registered_nodes():
            self.clear_preview("Preview unavailable without an editor graph.")
            return
        self._clear_graph()
        try:
            _ = self._create_variant_preview_node(record)
        except Exception as exc:
            self._clear_graph()
            logger.exception("Failed to render variant preview variant_id=%s", str(record.variantId))
            self._show_status(f"Failed to preview variant.\n{exc}")
            return
        self._finalize_loaded_preview()

    def _sync_registered_nodes(self) -> bool:
        host_graph = self._host_graph
        if not isinstance(host_graph, F8StudioGraph):
            return False
        preview_factory = self._preview_graph.node_factory
        preview_factory.clear_registered_nodes()
        for node_cls in list(host_graph.node_factory.nodes.values()):
            preview_factory.register_node(node_cls)
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

    def _show_graph(self) -> None:
        self._stack.setCurrentWidget(self._preview_graph.widget)

    def _finalize_loaded_preview(self) -> None:
        self._lock_preview_content()
        self._clear_selected_nodes()
        self._show_graph()
        self._focus_loaded_nodes()
        QtCore.QTimer.singleShot(0, self._focus_loaded_nodes)

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
        node_rects: list[QtCore.QRectF] = []
        for node in list(self._preview_graph.all_nodes() or []):
            try:
                node_rects.append(node.view.sceneBoundingRect())
            except (AttributeError, RuntimeError, TypeError):
                continue
        if not node_rects:
            scene = viewer.scene()
            if scene is None:
                return
            scene_rect = scene.itemsBoundingRect()
        else:
            scene_rect = QtCore.QRectF(node_rects[0])
            for rect in node_rects[1:]:
                scene_rect = scene_rect.united(rect)
        if scene_rect.isNull() or not scene_rect.isValid():
            return
        padding = 40.0
        padded_rect = QtCore.QRectF(scene_rect)
        padded_rect.adjust(-padding, -padding, padding, padding)
        viewer.fitInView(padded_rect, QtCore.Qt.AspectRatioMode.KeepAspectRatio)
        viewer.centerOn(padded_rect.center())
        viewer.viewport().update()

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
