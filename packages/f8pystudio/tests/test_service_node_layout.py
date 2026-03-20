from __future__ import annotations

from qtpy import QtCore

from f8pystudio.nodegraph.items import service_node_layout as layout


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
