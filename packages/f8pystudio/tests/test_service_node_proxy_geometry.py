from __future__ import annotations

from qtpy import QtGui, QtWidgets

from NodeGraphQt.constants import NodeEnum

from f8pystudio.nodegraph.service_basenode import F8StudioServiceNodeItem
from f8pystudio.nodegraph.viz_operator_nodeitem import F8StudioVizOperatorNodeItem
from f8pystudio.nodegraph.viewer import F8StudioNodeViewer


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


class _EmbeddedContentWidget(QtWidgets.QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.title_align = ""

    def setTitleAlign(self, value: str) -> None:
        self.title_align = str(value or "")


def _build_state_panel(item: F8StudioServiceNodeItem, *, name: str) -> QtWidgets.QGraphicsProxyWidget:
    header = QtWidgets.QWidget()
    header_layout = QtWidgets.QHBoxLayout(header)
    header_layout.setContentsMargins(0, 0, 0, 0)
    header_layout.addWidget(QtWidgets.QLabel("Header"))

    body = QtWidgets.QWidget()
    body_layout = QtWidgets.QVBoxLayout(body)
    body_layout.setContentsMargins(8, 0, 8, 6)
    body_layout.addWidget(QtWidgets.QLabel("Expanded body content"))
    body.setVisible(True)

    panel = QtWidgets.QWidget()
    panel_layout = QtWidgets.QVBoxLayout(panel)
    panel_layout.setContentsMargins(0, 0, 0, 0)
    panel_layout.setSpacing(0)
    panel_layout.addWidget(header)
    panel_layout.addWidget(body)

    proxy = QtWidgets.QGraphicsProxyWidget(item)
    proxy.setWidget(panel)
    proxy.setVisible(False)

    item._state_inline_proxies[name] = proxy
    item._state_inline_headers[name] = header
    item._state_inline_bodies[name] = body
    item._state_inline_expanded[name] = True
    item._state_inline_ctrl_serial[name] = "serial"
    return proxy


def test_hidden_state_panel_metric_uses_widget_geometry() -> None:
    _ensure_app()
    item = F8StudioServiceNodeItem(name="svc")
    _build_state_panel(item, name="alpha")
    item._prepare_layout_metrics()

    metric = item._measure_state_panel_metric("alpha", 180.0)

    assert metric.width >= 180.0
    assert metric.header_height > 0.0
    assert metric.height >= metric.header_height


def test_hidden_command_panel_metric_uses_widget_geometry() -> None:
    _ensure_app()
    item = F8StudioServiceNodeItem(name="svc")

    widget = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(QtWidgets.QPushButton("Run"))
    layout.addWidget(QtWidgets.QPushButton("Stop"))

    proxy = QtWidgets.QGraphicsProxyWidget(item)
    proxy.setWidget(widget)
    proxy.setVisible(False)

    item._cmd_proxy = proxy
    item._cmd_widget = widget
    item._cmd_serial = "cmd"
    item._prepare_layout_metrics()

    metric = item._measure_command_panel_metric(220.0)

    assert metric.width >= 220.0
    assert metric.height > 0.0


def test_hidden_command_panel_is_positioned_before_it_becomes_visible() -> None:
    _ensure_app()
    item = F8StudioServiceNodeItem(name="svc")

    widget = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(QtWidgets.QPushButton("Run"))

    proxy = QtWidgets.QGraphicsProxyWidget(item)
    proxy.setWidget(widget)
    proxy.setVisible(False)

    item._cmd_proxy = proxy
    item._cmd_widget = widget
    item._cmd_serial = "cmd"
    item._width = 240.0
    item._height = 180.0
    item._ports_end_y = 52.0
    item._prepare_layout_metrics()

    item._align_widgets_horizontal(18.0)

    assert proxy.pos().x() >= 4.0
    assert proxy.pos().y() >= 58.0


def test_hidden_embedded_widget_is_positioned_below_state_region() -> None:
    _ensure_app()
    item = F8StudioVizOperatorNodeItem(name="viz")

    content = _EmbeddedContentWidget()
    content.setMinimumSize(180, 96)
    proxy = QtWidgets.QGraphicsProxyWidget(item)
    proxy.setWidget(content)
    proxy.setVisible(False)

    item._widgets["plot"] = proxy
    item._width = 260.0
    item._height = 220.0
    item._ports_end_y = 64.0
    item._prepare_layout_metrics()

    item._align_widgets_horizontal(18.0)

    assert proxy.pos().y() >= 70.0


def test_viz_port_text_stays_hidden_even_when_display_name_is_true() -> None:
    _ensure_app()
    item = F8StudioVizOperatorNodeItem(name="viz")
    port = item.add_input(name="[D]text", display_name=True)
    text_item = item._input_items[port]

    text_item.setVisible(True)
    item._set_port_text_visibility(visible=True)

    assert text_item.isVisible() is False


def test_viz_calc_size_keeps_hidden_embedded_widget_geometry() -> None:
    _ensure_app()
    item = F8StudioVizOperatorNodeItem(name="viz")
    item._proxy_mode = True

    widget = QtWidgets.QWidget()
    widget.setMinimumSize(320, 180)
    layout = QtWidgets.QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(QtWidgets.QLabel("Plot"))

    proxy = QtWidgets.QGraphicsProxyWidget(item)
    proxy.setWidget(widget)
    proxy.setVisible(False)
    item._widgets["plot"] = proxy
    item._prepare_layout_metrics()

    width, height = item._calc_size_horizontal()

    assert width >= 320.0
    assert height > float(NodeEnum.HEIGHT.value)


class _ForcedProxyItem(F8StudioServiceNodeItem):
    def __init__(self) -> None:
        super().__init__(name="svc")
        self.target_proxy_mode = True

    def _should_enable_proxy_mode(self) -> bool:
        return bool(self.target_proxy_mode)


def test_sync_proxy_mode_force_hides_late_created_proxy_widgets() -> None:
    _ensure_app()
    item = _ForcedProxyItem()
    item.set_proxy_mode(True)
    proxy = _build_state_panel(item, name="alpha")
    item._prepare_layout_metrics()

    assert proxy.isVisible() is False

    proxy.setVisible(True)
    item.sync_proxy_mode(force=True)

    assert proxy.isVisible() is False
    assert item._text_item.isVisible() is False
    assert item._icon_item.isVisible() is False


def test_paint_does_not_trigger_proxy_state_changes(monkeypatch) -> None:
    _ensure_app()
    item = F8StudioServiceNodeItem(name="svc")
    pixmap = QtGui.QPixmap(200, 160)
    painter = QtGui.QPainter(pixmap)
    option = QtWidgets.QStyleOptionGraphicsItem()

    def _fail_auto_switch() -> None:
        raise AssertionError("paint must not call auto_switch_mode")

    monkeypatch.setattr(item, "auto_switch_mode", _fail_auto_switch)

    item.paint(painter, option, None)
    painter.end()


class _RecordingServiceNodeItem(F8StudioServiceNodeItem):
    def __init__(self) -> None:
        super().__init__(name="svc")
        self.sync_calls: list[bool] = []

    def sync_proxy_mode(self, *, force: bool = False) -> None:
        self.sync_calls.append(bool(force))


def test_viewer_refresh_auto_proxy_mode_calls_node_sync() -> None:
    _ensure_app()
    viewer = F8StudioNodeViewer()
    node_item = _RecordingServiceNodeItem()

    viewer.all_nodes = lambda: [node_item]  # type: ignore[method-assign]
    viewer.refresh_auto_proxy_mode(force=True)
    viewer.refresh_auto_proxy_mode(force=False)

    assert node_item.sync_calls == [True, False]
