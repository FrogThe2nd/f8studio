from __future__ import annotations

from typing import Any, Protocol, cast

from .items.embedded_resize_contract import ResizableEmbeddedWidget, clamp_content_size, content_rect_with_minimum


class _ServiceNodeLayoutHost(Protocol):
    @property
    def inputs(self) -> list[Any]: ...

    @property
    def outputs(self) -> list[Any]: ...

    def boundingRect(self) -> Any: ...

    def scene(self) -> Any: ...

    def _viewer_safe(self) -> Any | None: ...


class ServiceNodeLayoutMixin:
    def _content_rect_for_widgets(self, *, top_y: float) -> tuple[float, float, float, float]:
        host = cast(_ServiceNodeLayoutHost, self)
        rect = host.boundingRect()
        return content_rect_with_minimum(
            x=rect.left() + 4.0,
            y=top_y,
            width=rect.width() - 8.0,
            height=rect.bottom() - top_y - 4.0,
            minimum=(10, 10),
        )

    @staticmethod
    def _apply_widget_resize_policy(
        widget_proxy: Any,
        *,
        content_rect: tuple[float, float, float, float],
    ) -> bool:
        if not isinstance(widget_proxy, ResizableEmbeddedWidget):
            return False

        try:
            min_size = widget_proxy.minimum_content_size()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return False
        target_width, target_height = clamp_content_size(
            width=float(content_rect[2]),
            height=float(content_rect[3]),
            minimum=min_size,
        )
        try:
            widget_proxy.apply_content_rect(target_width, target_height)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return False

        proxy_widget = cast(Any, widget_proxy)
        try:
            proxy_widget.prepareGeometryChange()
        except (AttributeError, RuntimeError, TypeError):
            pass
        try:
            qwidget = proxy_widget.widget()
            if qwidget is None:
                return True
            qwidget.adjustSize()
        except (AttributeError, RuntimeError, TypeError):
            return True
        return True

    def _refresh_pipe_visual_state(self) -> None:
        host = cast(_ServiceNodeLayoutHost, self)
        ports = host.inputs + host.outputs
        seen_pipe_ids: set[int] = set()
        for port in ports:
            try:
                connected_pipes = list(port.connected_pipes)
            except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
                continue
            for pipe in connected_pipes:
                pipe_key = id(pipe)
                if pipe_key in seen_pipe_ids:
                    continue
                seen_pipe_ids.add(pipe_key)
                try:
                    pipe.update()
                except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
                    continue

        scene = host.scene()
        if scene is not None:
            try:
                scene.update()
            except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
                pass
        viewer = host._viewer_safe()
        if viewer is not None:
            try:
                viewer.viewport().update()
            except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
                pass
