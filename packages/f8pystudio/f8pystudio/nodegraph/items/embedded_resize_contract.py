from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ResizableEmbeddedWidget(Protocol):
    """
    Contract for node-embedded widgets that can resize from node content rect.
    """

    def minimum_content_size(self) -> tuple[int, int]:
        """
        Return minimum usable content size (width, height) in pixels.
        """

    def apply_content_rect(self, width: int, height: int) -> None:
        """
        Apply node-provided content size (width, height) in pixels.
        """


def clamp_content_size(
    *,
    width: float,
    height: float,
    minimum: tuple[int, int],
) -> tuple[int, int]:
    """
    Clamp candidate content size against minimum constraints.
    """
    min_w = max(1, int(minimum[0]))
    min_h = max(1, int(minimum[1]))
    out_w = max(min_w, int(width))
    out_h = max(min_h, int(height))
    return out_w, out_h


def content_rect_with_minimum(
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    minimum: tuple[int, int] = (10, 10),
) -> tuple[float, float, float, float]:
    """
    Normalize content rect and enforce minimum width/height.
    """
    min_w = max(1.0, float(minimum[0]))
    min_h = max(1.0, float(minimum[1]))
    out_w = max(min_w, float(width))
    out_h = max(min_h, float(height))
    return float(x), float(y), out_w, out_h

