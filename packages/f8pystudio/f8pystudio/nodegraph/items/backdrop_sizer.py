from __future__ import annotations

from qtpy import QtCore, QtWidgets

from NodeGraphQt.qgraphics.node_backdrop import BackdropSizer


class F8StudioBackdropSizer(BackdropSizer):
    """Resize handle that keeps Qt's selected movable item set single-handle."""

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == QtCore.Qt.LeftButton:
            self.begin_exclusive_resize_drag()
        super().mousePressEvent(event)

    def begin_exclusive_resize_drag(self) -> None:
        self._clear_other_selected_sizers()
        self.setSelected(True)

    def _clear_other_selected_sizers(self) -> None:
        scene = self.scene()
        if scene is None:
            return
        for item in list(scene.selectedItems() or []):
            if item is self:
                continue
            if isinstance(item, BackdropSizer):
                item.setSelected(False)
