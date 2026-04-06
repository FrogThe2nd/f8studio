from __future__ import annotations

from f8pystudio.nodegraph.service_basenode import F8StudioServiceNodeItem
import f8pystudio.nodegraph.service_basenode as service_basenode


class _NodeStub:
    pass


def test_service_node_item_delegates_content_rect_to_layout_helper(monkeypatch) -> None:
    node_item = _NodeStub()
    expected = (1.0, 2.0, 3.0, 4.0)
    calls: list[tuple[object, float]] = []

    def _fake_impl(target: object, *, top_y: float) -> tuple[float, float, float, float]:
        calls.append((target, top_y))
        return expected

    monkeypatch.setattr(service_basenode, "_content_rect_for_widgets_impl", _fake_impl)

    result = F8StudioServiceNodeItem._content_rect_for_widgets(node_item, top_y=12.5)

    assert result == expected
    assert calls == [(node_item, 12.5)]


def test_service_node_item_delegates_resize_policy_to_layout_helper(monkeypatch) -> None:
    node_item = _NodeStub()
    widget_proxy = object()
    content_rect = (0.0, 1.0, 2.0, 3.0)
    calls: list[tuple[object, tuple[float, float, float, float]]] = []

    def _fake_impl(target: object, *, content_rect: tuple[float, float, float, float]) -> bool:
        calls.append((target, content_rect))
        return True

    monkeypatch.setattr(service_basenode, "_apply_widget_resize_policy_impl", _fake_impl)

    result = F8StudioServiceNodeItem._apply_widget_resize_policy(node_item, widget_proxy, content_rect=content_rect)

    assert result is True
    assert calls == [(widget_proxy, content_rect)]


def test_service_node_item_delegates_tooltip_disable_to_painting_helper(monkeypatch) -> None:
    node_item = _NodeStub()
    calls: list[tuple[object, bool]] = []

    def _fake_impl(target: object, state: bool) -> None:
        calls.append((target, state))

    monkeypatch.setattr(service_basenode, "_tooltip_disable_impl", _fake_impl)

    F8StudioServiceNodeItem._tooltip_disable(node_item, True)

    assert calls == [(node_item, True)]


def test_service_node_item_delegates_pipe_refresh_to_interaction_helper(monkeypatch) -> None:
    node_item = _NodeStub()
    calls: list[object] = []

    def _fake_impl(target: object) -> None:
        calls.append(target)

    monkeypatch.setattr(service_basenode, "_refresh_pipe_visual_state_impl", _fake_impl)

    F8StudioServiceNodeItem._refresh_pipe_visual_state(node_item)

    assert calls == [node_item]
