from __future__ import annotations

from qtpy import QtCore, QtWidgets

from NodeGraphQt.widgets.viewer import NodeViewer

from f8pystudio.nodegraph.viewer import F8StudioNodeViewer


class _FakeSceneMouseEvent:
    def __init__(self, scene_pos: QtCore.QPointF, *, button: QtCore.Qt.MouseButton = QtCore.Qt.LeftButton) -> None:
        self._scene_pos = scene_pos
        self._button = button
        self.accepted = False

    def scenePos(self) -> QtCore.QPointF:
        return self._scene_pos

    def button(self) -> QtCore.Qt.MouseButton:
        return self._button

    def accept(self) -> None:
        self.accepted = True


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


def _proxy_hit_position(viewer: F8StudioNodeViewer) -> QtCore.QPointF:
    scene = viewer.scene()
    assert scene is not None

    button = QtWidgets.QPushButton("toolbar")
    button.resize(120, 30)
    button.show()

    proxy = QtWidgets.QGraphicsProxyWidget()
    proxy.setWidget(button)
    proxy.setPos(30.0, 40.0)
    scene.addItem(proxy)
    return proxy.mapToScene(proxy.boundingRect().center())


def test_scene_mouse_press_over_proxy_does_not_delegate_to_base(monkeypatch) -> None:
    _ensure_app()
    viewer = F8StudioNodeViewer()
    viewer.resize(640, 480)
    viewer.show()
    QtWidgets.QApplication.processEvents()

    scene_pos = _proxy_hit_position(viewer)
    called = {"count": 0}

    def _fake_base(_self: NodeViewer, _event: object) -> None:
        called["count"] += 1

    monkeypatch.setattr(NodeViewer, "sceneMousePressEvent", _fake_base)

    viewer.sceneMousePressEvent(_FakeSceneMouseEvent(scene_pos))

    assert called["count"] == 0


def test_scene_mouse_press_over_proxy_cancels_live_pipe(monkeypatch) -> None:
    _ensure_app()
    viewer = F8StudioNodeViewer()
    viewer.resize(640, 480)
    viewer.show()
    QtWidgets.QApplication.processEvents()

    scene_pos = _proxy_hit_position(viewer)
    viewer._LIVE_PIPE.setVisible(True)
    called = {"count": 0}

    def _fake_end_live_connection() -> None:
        called["count"] += 1

    monkeypatch.setattr(viewer, "end_live_connection", _fake_end_live_connection)

    viewer.sceneMousePressEvent(_FakeSceneMouseEvent(scene_pos))

    assert called["count"] == 1


def test_scene_mouse_press_without_proxy_delegates_to_base(monkeypatch) -> None:
    _ensure_app()
    viewer = F8StudioNodeViewer()
    viewer.resize(640, 480)
    viewer.show()
    QtWidgets.QApplication.processEvents()

    called = {"count": 0}

    def _fake_base(_self: NodeViewer, _event: object) -> None:
        called["count"] += 1

    monkeypatch.setattr(NodeViewer, "sceneMousePressEvent", _fake_base)

    viewer.sceneMousePressEvent(_FakeSceneMouseEvent(QtCore.QPointF(-10_000.0, -10_000.0)))

    assert called["count"] == 1
