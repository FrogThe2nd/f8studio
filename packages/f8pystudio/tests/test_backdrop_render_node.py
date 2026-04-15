from __future__ import annotations

from qtpy import QtWidgets

from f8pystudio.render_nodes.backdrop import BackdropRenderNode


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


def test_backdrop_render_node_update_model_skips_widget_sync() -> None:
    _ensure_app()
    node = BackdropRenderNode()

    node.view.width = 420.0
    node.view.height = 240.0
    node.view.name = "Backdrop Region"

    node.update_model()

    assert node.model.width == 420.0
    assert node.model.height == 240.0
    assert node.model.name == "Backdrop Region"
