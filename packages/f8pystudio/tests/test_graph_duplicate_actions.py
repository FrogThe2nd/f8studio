from __future__ import annotations

from f8pystudio.nodegraph.graph_duplicate_actions import GraphDuplicateActionsMixin


class _NodeStub:
    type_ = "test.node"

    def __init__(self, *, ui_state: dict[str, object]) -> None:
        self._ui_state = dict(ui_state)
        self._copied_ui_state: dict[str, object] | None = None
        self.spec = None

    def ui_state(self) -> dict[str, object]:
        return dict(self._ui_state)

    def set_ui_state(self, value: dict[str, object] | None) -> None:
        self._copied_ui_state = dict(value or {})

    def ui_overrides(self) -> dict[str, object]:
        return {}


def test_duplicate_copy_clears_global_hotkeys_from_ui_state() -> None:
    source = _NodeStub(ui_state={"stateFieldHotkeys": {"trigger": "Ctrl+Alt+P"}, "stateInlineExpanded": {"trigger": True}})
    target = _NodeStub(ui_state={})

    GraphDuplicateActionsMixin._copy_node_spec_and_ui(source=source, target=target)

    assert target._copied_ui_state == {"stateInlineExpanded": {"trigger": True}}
