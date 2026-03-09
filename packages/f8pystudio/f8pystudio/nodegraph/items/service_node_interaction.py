from __future__ import annotations

from typing import Any


def refresh_pipe_visual_state(node_item: Any) -> None:
    """
    Force connected pipes to repaint after disabled state changes.
    """
    ports = node_item.inputs + node_item.outputs
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

    scene = node_item.scene()
    if scene is not None:
        try:
            scene.update()
        except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
            pass
    viewer = node_item._viewer_safe()
    if viewer is not None:
        try:
            viewer.viewport().update()
        except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
            pass
