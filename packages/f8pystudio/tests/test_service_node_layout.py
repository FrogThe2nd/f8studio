from __future__ import annotations

from qtpy import QtCore, QtWidgets

from f8pystudio.nodegraph.items import service_node_layout as layout
from f8pystudio.nodegraph.service_basenode import F8StudioServiceNodeItem


class _NodeItemStub:
    def boundingRect(self) -> QtCore.QRectF:
        return QtCore.QRectF(0.0, 0.0, 120.0, 80.0)


class _ResizableWidgetProxy:
    def __init__(self) -> None:
        self.applied: tuple[int, int] | None = None
        self.prepare_calls = 0

    def minimum_content_size(self) -> tuple[int, int]:
        return 40, 20

    def apply_content_rect(self, width: int, height: int) -> None:
        self.applied = int(width), int(height)

    def prepareGeometryChange(self) -> None:
        self.prepare_calls += 1

    def widget(self):  # noqa: ANN201
        return None


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


class _DeferredDrawStub:
    def __init__(self) -> None:
        self.id = "stub"
        self._deferred_layout_pending = False
        self._in_draw_node = False
        self.draw_calls = 0

    def draw_node(self) -> None:
        self.draw_calls += 1


class _PanelMeasureStub:
    def __init__(self, *, proxy: QtWidgets.QGraphicsProxyWidget) -> None:
        self._state_inline_proxies = {"code": proxy}

    def boundingRect(self) -> QtCore.QRectF:
        return QtCore.QRectF(0.0, 0.0, 220.0, 120.0)


def test_content_rect_for_widgets_respects_inner_padding() -> None:
    node_item = _NodeItemStub()

    rect = layout.content_rect_for_widgets(node_item, top_y=10.0)

    assert rect == (4.0, 10.0, 112.0, 66.0)


def test_apply_widget_resize_policy_non_resizable_returns_false() -> None:
    ok = layout.apply_widget_resize_policy(object(), content_rect=(0.0, 0.0, 100.0, 50.0))

    assert ok is False


def test_apply_widget_resize_policy_resizable_applies_clamped_size() -> None:
    proxy = _ResizableWidgetProxy()

    ok = layout.apply_widget_resize_policy(proxy, content_rect=(0.0, 0.0, 10.0, 5.0))

    assert ok is True
    assert proxy.applied == (40, 20)
    assert proxy.prepare_calls == 1


def test_schedule_deferred_draw_node_debounces_within_one_event_loop_tick() -> None:
    app = _ensure_app()
    stub = _DeferredDrawStub()

    F8StudioServiceNodeItem._schedule_deferred_draw_node(stub)  # type: ignore[arg-type]
    F8StudioServiceNodeItem._schedule_deferred_draw_node(stub)  # type: ignore[arg-type]
    app.processEvents()

    assert stub.draw_calls == 1
    assert stub._deferred_layout_pending is False


def test_measure_state_panel_height_prefers_proxy_measured_height() -> None:
    _ensure_app()
    scene = QtWidgets.QGraphicsScene()
    parent = QtWidgets.QGraphicsRectItem()
    scene.addItem(parent)
    proxy = QtWidgets.QGraphicsProxyWidget(parent)
    panel = QtWidgets.QWidget()
    panel_layout = QtWidgets.QVBoxLayout(panel)
    panel_layout.setContentsMargins(0, 0, 0, 0)
    header = QtWidgets.QLabel("header", panel)
    header.setFixedHeight(20)
    body = QtWidgets.QLabel("body", panel)
    body.setFixedHeight(120)
    panel_layout.addWidget(header)
    panel_layout.addWidget(body)
    proxy.setWidget(panel)

    stub = _PanelMeasureStub(proxy=proxy)
    panel_h = F8StudioServiceNodeItem._measure_state_panel_height(  # type: ignore[arg-type]
        stub,
        "code",
        default_header_h=20.0,
        target_inner_w=200.0,
    )
    assert panel_h > 20.0
