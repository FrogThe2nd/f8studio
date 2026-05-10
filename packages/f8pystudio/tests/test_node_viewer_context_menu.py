from __future__ import annotations

from qtpy import QtCore, QtWidgets

from f8pystudio.nodegraph.viewer import F8StudioNodeViewer


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


class _FakeSignal:
    def __init__(self) -> None:
        self.callbacks: list[object] = []

    def connect(self, callback: object, _connection_type: object = None) -> None:
        self.callbacks.append(callback)


class _FakeMenu:
    def __init__(self, *, enabled: bool, actions: list[QtWidgets.QAction] | None = None) -> None:
        self.aboutToHide = _FakeSignal()
        self.popup_calls: list[QtCore.QPoint] = []
        self.exec_calls: list[QtCore.QPoint] = []
        self._enabled = bool(enabled)
        self._actions = list(actions or [])

    def isEnabled(self) -> bool:
        return bool(self._enabled)

    def actions(self) -> list[QtWidgets.QAction]:
        return list(self._actions)

    def popup(self, pos: QtCore.QPoint) -> None:
        self.popup_calls.append(QtCore.QPoint(pos))

    def exec_(self, pos: QtCore.QPoint) -> object | None:
        self.exec_calls.append(QtCore.QPoint(pos))
        return self._actions[0] if self._actions else None


def test_context_menu_popup_is_non_blocking_for_video_timers() -> None:
    _ensure_app()
    viewer = F8StudioNodeViewer()
    viewer.resize(640, 480)
    viewer.show()
    assert viewer.viewportUpdateMode() == QtWidgets.QGraphicsView.BoundingRectViewportUpdate
    graph_menu = _FakeMenu(enabled=True, actions=[QtWidgets.QAction("Noop", viewer)])
    nodes_menu = _FakeMenu(enabled=False)
    viewer.context_menus = lambda: {"graph": graph_menu, "nodes": nodes_menu}  # type: ignore[method-assign]

    handled = viewer._popup_context_menu(QtCore.QPoint(10, 20))

    assert handled is True
    assert graph_menu.popup_calls == [QtCore.QPoint(10, 20)]
    assert graph_menu.exec_calls == []
    assert len(graph_menu.aboutToHide.callbacks) == 1
    assert viewer.is_context_menu_selection_pending() is True
    assert viewer.viewportUpdateMode() == QtWidgets.QGraphicsView.NoViewportUpdate

    viewer._on_context_menu_hidden()

    assert viewer.is_context_menu_selection_pending() is False
    assert viewer.viewportUpdateMode() == QtWidgets.QGraphicsView.BoundingRectViewportUpdate
