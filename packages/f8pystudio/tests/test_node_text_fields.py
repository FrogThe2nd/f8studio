from __future__ import annotations

from typing import Any

from f8pystudio.nodegraph import node_text_fields
from f8pystudio.nodegraph.node_text_fields import (
    get_node_text,
    node_text_editor_binding,
    node_text_target_exists,
    set_node_text,
)


class _TextNode:
    def __init__(self, *, code: str) -> None:
        self.code = code
        self.writes: list[tuple[str, Any, bool | None]] = []

    def get_property(self, name: str) -> Any:
        if name != "code":
            raise KeyError(name)
        return self.code

    def set_property(self, name: str, value: Any, *, push_undo: bool = True) -> None:
        if name != "code":
            raise KeyError(name)
        self.code = str(value or "")
        self.writes.append((name, value, push_undo))


class _TextGraph:
    def __init__(self, node: _TextNode | None) -> None:
        self.node = node

    def get_node_by_id(self, node_id: str) -> _TextNode | None:
        if str(node_id or "") != "nodeA":
            return None
        return self.node


def test_set_node_text_resolves_current_node_by_id_after_replacement() -> None:
    old_node = _TextNode(code="old\n")
    graph = _TextGraph(old_node)

    assert get_node_text(graph, "nodeA", "code") == "old\n"

    replacement = _TextNode(code="replacement\n")
    graph.node = replacement

    assert set_node_text(graph, "nodeA", "code", "updated\n", push_undo=True) is True
    assert old_node.code == "old\n"
    assert replacement.code == "updated\n"
    assert replacement.writes == [("code", "updated\n", True)]


def test_node_text_editor_binding_resolves_current_node_and_exposes_session_key() -> None:
    graph = _TextGraph(_TextNode(code="initial\n"))
    binding = node_text_editor_binding(graph, "nodeA", "code")

    assert binding is not None
    assert binding.session_key.node_id == "nodeA"
    assert binding.session_key.field_name == "code"
    assert binding.value_getter() == "initial\n"

    graph.node = _TextNode(code="replacement\n")

    assert binding.target_exists() is True
    assert binding.value_setter("updated\n") is True
    assert graph.node.code == "updated\n"
    assert graph.node.writes == [("code", "updated\n", True)]


def test_node_text_target_missing_after_delete_reports_failure(monkeypatch) -> None:
    graph = _TextGraph(_TextNode(code="live\n"))
    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(
        node_text_fields,
        "show_warning",
        lambda _parent, title, message: warnings.append((str(title), str(message))),
    )

    assert node_text_target_exists(graph, "nodeA", "code") is True

    graph.node = None

    assert node_text_target_exists(graph, "nodeA", "code") is False
    assert set_node_text(graph, "nodeA", "code", "lost\n", warning_parent=None) is False
    assert warnings == [
        (
            "Code Save Failed",
            "Target node/field not found.\nnodeId=nodeA\nfield=code",
        )
    ]
