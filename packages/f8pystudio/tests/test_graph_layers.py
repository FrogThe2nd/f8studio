from __future__ import annotations

from types import SimpleNamespace

import pytest

import f8pystudio.nodegraph.graph_layering as graph_layering
from f8pystudio.nodegraph.graph_layering import GraphLayeringMixin
from f8pystudio.nodegraph.layers import F8LayerDef, normalize_layer_defs


class _Signal:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def emit(self, *args: object) -> None:
        self.calls.append(tuple(args))


class _FakeView:
    def __init__(self) -> None:
        self.visible = True

    def setVisible(self, visible: bool) -> None:
        self.visible = bool(visible)

    def isVisible(self) -> bool:
        return bool(self.visible)


class _FakeNode:
    def __init__(self, node_id: str, *, layer_ids: list[str] | None = None) -> None:
        self.id = node_id
        self._ui_state = {}
        if layer_ids is not None:
            self._ui_state["layerIds"] = list(layer_ids)
        self.view = _FakeView()
        self.selected = True

    def ui_state(self) -> dict[str, object]:
        return dict(self._ui_state)

    def set_property(self, name: str, value: object, push_undo: bool = True) -> None:
        _ = push_undo
        if name == "f8_ui_state":
            self._ui_state = dict(value) if isinstance(value, dict) else {}
        if name == "selected":
            self.selected = bool(value)


class _FakePipe:
    def __init__(self, out_node: _FakeNode, in_node: _FakeNode) -> None:
        self.output_port = SimpleNamespace(node=SimpleNamespace(id=out_node.id))
        self.input_port = SimpleNamespace(node=SimpleNamespace(id=in_node.id))


class _FakeViewer:
    def __init__(self, pipes: list[_FakePipe] | None = None) -> None:
        self._pipes = list(pipes or [])
        self.refresh_calls = 0
        self.viewport_update_calls = 0

    def all_pipes(self) -> list[_FakePipe]:
        return list(self._pipes)

    def refresh_edge_visibility(self) -> None:
        self.refresh_calls += 1

    def viewport(self) -> "_FakeViewer":
        return self

    def update(self) -> None:
        self.viewport_update_calls += 1


class _FakeGraph(GraphLayeringMixin):
    def __init__(self, nodes: list[_FakeNode], viewer: _FakeViewer | None = None) -> None:
        self._nodes = list(nodes)
        self._viewer = viewer
        self._session_layer_defs = normalize_layer_defs(())
        self._active_layer_ids = ("base",)
        self.layers_changed = _Signal()
        self.active_layers_changed = _Signal()

    def all_nodes(self) -> list[_FakeNode]:
        return list(self._nodes)

    def viewer(self) -> _FakeViewer | None:
        return self._viewer


@pytest.fixture(autouse=True)
def _patch_viewer_types(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(graph_layering, "F8StudioNodeViewer", _FakeViewer)


def test_refresh_layer_visibility_hides_non_active_nodes_and_clears_selection() -> None:
    layer_one = F8LayerDef(id="logic", label="Logic", default_visible=True)
    layer_two = F8LayerDef(id="io", label="I/O", default_visible=False)
    node_visible = _FakeNode("n1", layer_ids=["logic"])
    node_hidden = _FakeNode("n2", layer_ids=["io"])
    graph = _FakeGraph([node_visible, node_hidden], viewer=_FakeViewer())

    graph.set_session_layer_defs([layer_one, layer_two], preserve_active=False)
    graph.set_active_layer_ids(("logic",))

    assert node_visible.view.visible is True
    assert node_hidden.view.visible is False
    assert node_hidden.selected is False


def test_refresh_layer_visibility_keeps_edge_refresh_without_hidden_link_badge() -> None:
    layer_one = F8LayerDef(id="logic", label="Logic", default_visible=True)
    layer_two = F8LayerDef(id="io", label="I/O", default_visible=False)
    node_left = _FakeNode("left", layer_ids=["logic"])
    node_right = _FakeNode("right", layer_ids=["io"])
    viewer = _FakeViewer(pipes=[_FakePipe(node_left, node_right)])
    graph = _FakeGraph([node_left, node_right], viewer=viewer)

    graph.set_session_layer_defs([layer_one, layer_two], preserve_active=False)
    graph.set_active_layer_ids(("logic",))

    assert viewer.refresh_calls >= 1
    assert viewer.viewport_update_calls >= 1


def test_delete_layer_moves_orphaned_nodes_back_to_base() -> None:
    layer_one = F8LayerDef(id="logic", label="Logic", default_visible=True)
    node = _FakeNode("n1", layer_ids=["logic"])
    graph = _FakeGraph([node], viewer=_FakeViewer())

    graph.set_session_layer_defs([layer_one], preserve_active=False)
    graph.delete_layer("logic")

    assert graph.node_layer_ids(node) == ("base",)
    assert [layer.id for layer in graph.session_layer_defs()] == ["base"]


def test_base_default_visible_false_is_preserved_in_layer_defs_and_active_defaults() -> None:
    graph = _FakeGraph([], viewer=_FakeViewer())

    graph.set_session_layer_defs(
        [
            F8LayerDef(id="base", label="Base", default_visible=False, is_base=True),
            F8LayerDef(id="control", label="Control", default_visible=True),
        ],
        preserve_active=False,
    )

    base_layer = graph.layer_def_by_id("base")
    assert base_layer is not None
    assert base_layer.default_visible is False
    assert graph.default_visible_layer_ids() == ("control",)
    assert graph.active_layer_ids() == ("control",)


def test_active_layers_can_be_empty_without_falling_back_to_base() -> None:
    node = _FakeNode("n1", layer_ids=["base"])
    graph = _FakeGraph([node], viewer=_FakeViewer())

    graph.set_active_layer_ids(())

    assert graph.active_layer_ids() == ()
    assert node.view.visible is False
    assert node.selected is False


def test_default_visible_layers_can_be_empty_without_reactivating_base() -> None:
    graph = _FakeGraph([], viewer=_FakeViewer())

    graph.set_session_layer_defs(
        [
            F8LayerDef(id="base", label="Base", default_visible=False, is_base=True),
            F8LayerDef(id="control", label="Control", default_visible=False),
        ],
        preserve_active=False,
    )

    assert graph.default_visible_layer_ids() == ()
    assert graph.active_layer_ids() == ()
