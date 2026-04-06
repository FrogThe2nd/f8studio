from __future__ import annotations

from f8pystudio.nodegraph.items import service_node_interaction as interaction


class _PipeStub:
    def __init__(self) -> None:
        self.update_calls = 0

    def update(self) -> None:
        self.update_calls += 1


class _PortStub:
    def __init__(self, connected_pipes: list[_PipeStub]) -> None:
        self.connected_pipes = connected_pipes


class _SceneStub:
    def __init__(self) -> None:
        self.update_calls = 0

    def update(self) -> None:
        self.update_calls += 1


class _ViewportStub:
    def __init__(self) -> None:
        self.update_calls = 0

    def update(self) -> None:
        self.update_calls += 1


class _ViewerStub:
    def __init__(self) -> None:
        self._viewport = _ViewportStub()

    def viewport(self) -> _ViewportStub:
        return self._viewport


class _NodeItemStub:
    def __init__(self, pipe_a: _PipeStub, pipe_b: _PipeStub) -> None:
        self.inputs = [_PortStub([pipe_a, pipe_b])]
        self.outputs = [_PortStub([pipe_a])]
        self._scene = _SceneStub()
        self._viewer = _ViewerStub()

    def scene(self) -> _SceneStub:
        return self._scene

    def _viewer_safe(self) -> _ViewerStub:
        return self._viewer


def test_refresh_pipe_visual_state_updates_each_connected_pipe_once() -> None:
    pipe_a = _PipeStub()
    pipe_b = _PipeStub()
    node_item = _NodeItemStub(pipe_a, pipe_b)

    interaction.refresh_pipe_visual_state(node_item)

    assert pipe_a.update_calls == 1
    assert pipe_b.update_calls == 1
    assert node_item.scene().update_calls == 1
    assert node_item._viewer_safe().viewport().update_calls == 1
