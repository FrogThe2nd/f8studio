from __future__ import annotations

from typing import Any

from .embedded_resize_contract import ResizableEmbeddedWidget, clamp_content_size, content_rect_with_minimum


def content_rect_for_widgets(node_item: Any, *, top_y: float) -> tuple[float, float, float, float]:
    """
    Compute available node-inner content rect for embedded widgets.
    """
    rect = node_item.boundingRect()
    return content_rect_with_minimum(
        x=rect.left() + 4.0,
        y=top_y,
        width=rect.width() - 8.0,
        height=rect.bottom() - top_y - 4.0,
        minimum=(10, 10),
    )


def apply_widget_resize_policy(
    widget_proxy: Any,
    *,
    content_rect: tuple[float, float, float, float],
) -> bool:
    """
    Apply optional node->widget resize contract.

    Returns:
        bool: True when resize was applied via `ResizableEmbeddedWidget`.
    """
    if not isinstance(widget_proxy, ResizableEmbeddedWidget):
        return False

    try:
        min_size = widget_proxy.minimum_content_size()
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False
    target_w, target_h = clamp_content_size(
        width=float(content_rect[2]),
        height=float(content_rect[3]),
        minimum=min_size,
    )
    try:
        widget_proxy.apply_content_rect(target_w, target_h)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False

    try:
        widget_proxy.prepareGeometryChange()
    except (AttributeError, RuntimeError, TypeError):
        pass
    try:
        qwidget = widget_proxy.widget()
        if qwidget is None:
            return True
        qwidget.adjustSize()
    except (AttributeError, RuntimeError, TypeError):
        return True
    return True
